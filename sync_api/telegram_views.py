"""
Telegram webhook endpoint + bot logic for Super Productivity.

Voice/text note -> Gemini -> task inserted into the right project (Inbox fallback)
inside the user's sync-data.json; inline buttons to complete / delete / move.
Single-account by design (configured via .env); see settings SP_* / TELEGRAM_* /
GEMINI_*.
"""
from __future__ import annotations

import hmac
import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import gemini, sp_sync, telegram

log = logging.getLogger("sync_api.telegram")


# --- config -----------------------------------------------------------------
def _cfg():
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN .env da sozlanmagan")
    return {
        "token": token,
        "secret": getattr(settings, "TELEGRAM_WEBHOOK_SECRET", ""),
        "gemini_key": getattr(settings, "GEMINI_API_KEY", ""),
        "gemini_model": getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash"),
        "sp_user": getattr(settings, "SP_SYNC_USERNAME", "admin"),
        "sp_path": getattr(settings, "SP_SYNC_FILE_PATH", "/sync-data.json"),
        "sp_pw": getattr(settings, "SP_SYNC_ENCRYPTION_PASSWORD", "") or None,
        "client_id": getattr(settings, "SP_BOT_CLIENT_ID", "tgbot0"),
    }


# --- UI helpers -------------------------------------------------------------
def _task_buttons(task_id: str) -> dict:
    return telegram.inline_keyboard([
        [("✅ Bajarildi", f"done:{task_id}"), ("🗑 O'chirish", f"del:{task_id}")],
        [("📁 Boshqa project", f"mv:{task_id}")],
    ])


def _menu_buttons() -> dict:
    return telegram.inline_keyboard([
        [("📋 Bugungi tasklar", "today")],
        [("📁 Projectlar", "projects")],
    ])


WELCOME = (
    "👋 <b>Super Productivity bot</b>\n\n"
    "• Ovozli xabar yoki matn yuboring — men uni vazifaga aylantirib, mos "
    "projectga qo'shaman (topilmasa Inbox'ga).\n"
    "• Tugmalar bilan bajarildi/o'chirish/ko'chirishni boshqaring.\n\n"
    "Boshlash uchun biror narsa yozing yoki ovoz yuboring."
)


# --- core actions (DB-locked) ----------------------------------------------
def _add_from_parsed(cfg, parsed) -> tuple[str, str]:
    """Returns (task_id, reply_html)."""
    title = parsed.get("title") or ""
    if not title.strip():
        raise sp_sync.SpSyncError("Vazifa matnini ajratib bo'lmadi. Qaytadan urinib ko'ring.")
    with transaction.atomic():
        sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
        pid, pname = sp_sync.resolve_project_id(env, parsed.get("project"))
        task = sp_sync.add_task(env, title, pid, cfg["client_id"],
                                due_today=bool(parsed.get("due_today")))
        sp_sync.save_envelope(sf, env, flags, cfg["sp_pw"])
    today = " · 📅 bugun" if parsed.get("due_today") else ""
    reply = f"✅ Qo'shildi: <b>{_esc(title)}</b>\n📁 {_esc(pname)}{today}"
    return task["id"], reply


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- update handlers --------------------------------------------------------
def _handle_message(cfg, msg):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if text in ("/start", "/menu") or text.startswith("/start") or text.startswith("/menu"):
        telegram.send_message(cfg["token"], chat_id, WELCOME, reply_markup=_menu_buttons())
        return
    if text in ("/today",):
        _send_today(cfg, chat_id)
        return
    if text in ("/projects",):
        _send_projects(cfg, chat_id)
        return

    # voice note
    voice = msg.get("voice") or msg.get("audio")
    if voice:
        if not cfg["gemini_key"]:
            telegram.send_message(cfg["token"], chat_id, "⚠️ GEMINI_API_KEY sozlanmagan.")
            return
        try:
            fp = telegram.get_file_path(cfg["token"], voice["file_id"])
            audio = telegram.download_file(cfg["token"], fp)
            mime = voice.get("mime_type") or "audio/ogg"
            sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
            names = [p["title"] for p in sp_sync.list_projects(env)]
            parsed = gemini.transcribe_voice(audio, mime, names, cfg["gemini_key"], cfg["gemini_model"])
            task_id, reply = _add_from_parsed(cfg, parsed)
            telegram.send_message(cfg["token"], chat_id, reply, reply_markup=_task_buttons(task_id))
        except (sp_sync.SpSyncError, gemini.GeminiError) as exc:
            telegram.send_message(cfg["token"], chat_id, f"⚠️ {exc}")
        return

    # plain text -> task
    if text:
        try:
            parsed = {"title": text, "project": None, "due_today": False}
            if cfg["gemini_key"]:
                sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
                names = [p["title"] for p in sp_sync.list_projects(env)]
                try:
                    parsed = gemini.parse_text(text, names, cfg["gemini_key"], cfg["gemini_model"])
                except gemini.GeminiError:
                    parsed = {"title": text, "project": None, "due_today": False}
            task_id, reply = _add_from_parsed(cfg, parsed)
            telegram.send_message(cfg["token"], chat_id, reply, reply_markup=_task_buttons(task_id))
        except sp_sync.SpSyncError as exc:
            telegram.send_message(cfg["token"], chat_id, f"⚠️ {exc}")


def _handle_callback(cfg, cq):
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    cq_id = cq["id"]

    try:
        if data == "today":
            telegram.answer_callback_query(cfg["token"], cq_id)
            _send_today(cfg, chat_id)
            return
        if data == "projects":
            telegram.answer_callback_query(cfg["token"], cq_id)
            _send_projects(cfg, chat_id)
            return

        action, _, rest = data.partition(":")

        if action in ("done", "undone"):
            done = action == "done"
            with transaction.atomic():
                sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
                sp_sync.set_task_done(env, rest, done, cfg["client_id"])
                sp_sync.save_envelope(sf, env, flags, cfg["sp_pw"])
            telegram.answer_callback_query(cfg["token"], cq_id, "Bajarildi ✅" if done else "Qaytarildi")
            toggle = ("↩️ Qaytarish", f"undone:{rest}") if done else ("✅ Bajarildi", f"done:{rest}")
            kb = telegram.inline_keyboard([[toggle, ("🗑 O'chirish", f"del:{rest}")]])
            telegram.edit_message_text(cfg["token"], chat_id, message_id,
                                       ("✅ <s>Bajarildi</s>" if done else "🔄 Qayta ochildi"),
                                       reply_markup=kb)
            return

        if action == "del":
            with transaction.atomic():
                sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
                sp_sync.delete_task(env, rest, cfg["client_id"])
                sp_sync.save_envelope(sf, env, flags, cfg["sp_pw"])
            telegram.answer_callback_query(cfg["token"], cq_id, "O'chirildi 🗑")
            telegram.edit_message_text(cfg["token"], chat_id, message_id, "🗑 <s>O'chirildi</s>")
            return

        if action == "mv":
            sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
            projects = sp_sync.list_projects(env)
            rows = [[(p["title"], f"mvto:{rest}:{i}")] for i, p in enumerate(projects[:12])]
            telegram.answer_callback_query(cfg["token"], cq_id)
            telegram.edit_message_text(cfg["token"], chat_id, message_id,
                                       "📁 Qaysi projectga ko'chiramiz?",
                                       reply_markup=telegram.inline_keyboard(rows))
            return

        if action == "mvto":
            task_id, _, idx_s = rest.rpartition(":")
            with transaction.atomic():
                sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
                projects = sp_sync.list_projects(env)
                idx = int(idx_s)
                if idx < 0 or idx >= len(projects):
                    raise sp_sync.SpSyncError("Project topilmadi.")
                target = projects[idx]
                sp_sync.move_task(env, task_id, target["id"], cfg["client_id"])
                sp_sync.save_envelope(sf, env, flags, cfg["sp_pw"])
            telegram.answer_callback_query(cfg["token"], cq_id, f"→ {target['title']}")
            telegram.edit_message_text(cfg["token"], chat_id, message_id,
                                       f"📁 Ko'chirildi → <b>{_esc(target['title'])}</b>",
                                       reply_markup=_task_buttons(task_id))
            return

        telegram.answer_callback_query(cfg["token"], cq_id)
    except (sp_sync.SpSyncError, gemini.GeminiError) as exc:
        telegram.answer_callback_query(cfg["token"], cq_id, "Xato")
        if chat_id:
            telegram.send_message(cfg["token"], chat_id, f"⚠️ {exc}")


def _send_today(cfg, chat_id):
    try:
        sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
        tasks = sp_sync.list_today_tasks(env)
    except sp_sync.SpSyncError as exc:
        telegram.send_message(cfg["token"], chat_id, f"⚠️ {exc}")
        return
    if not tasks:
        telegram.send_message(cfg["token"], chat_id, "📋 Bugun uchun ochiq task yo'q.")
        return
    telegram.send_message(cfg["token"], chat_id, f"📋 <b>Bugungi tasklar ({len(tasks)})</b>")
    for t in tasks:
        kb = telegram.inline_keyboard([[("✅ Bajarildi", f"done:{t['id']}"),
                                        ("🗑 O'chirish", f"del:{t['id']}")]])
        telegram.send_message(cfg["token"], chat_id, f"• {_esc(t['title'])}", reply_markup=kb)


def _send_projects(cfg, chat_id):
    try:
        sf, env, flags = sp_sync.load_envelope(cfg["sp_user"], cfg["sp_path"], cfg["sp_pw"])
        projects = sp_sync.list_projects(env)
    except sp_sync.SpSyncError as exc:
        telegram.send_message(cfg["token"], chat_id, f"⚠️ {exc}")
        return
    lines = "\n".join(f"• {_esc(p['title'])}" for p in projects) or "(yo'q)"
    telegram.send_message(cfg["token"], chat_id, f"📁 <b>Projectlar</b>\n{lines}")


# --- the webhook endpoint ---------------------------------------------------
@csrf_exempt
def webhook(request, secret: str = ""):
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        cfg = _cfg()
    except RuntimeError as exc:
        log.error("telegram config error: %s", exc)
        return JsonResponse({"error": str(exc)}, status=503)

    # Defense in depth: path secret AND Telegram's header secret (if configured).
    expected = cfg["secret"]
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    path_ok = hmac.compare_digest(secret or "", expected or "")
    header_ok = hmac.compare_digest(header or "", expected or "")
    if expected and not (path_ok or header_ok):
        return HttpResponseForbidden("bad webhook secret")

    try:
        update = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)

    try:
        if "message" in update:
            _handle_message(cfg, update["message"])
        elif "callback_query" in update:
            _handle_callback(cfg, update["callback_query"])
    except Exception:  # never 500 back to Telegram (it would retry forever)
        log.exception("telegram webhook handler failed")

    return HttpResponse("ok")
