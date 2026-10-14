"""
Minimal Telegram Bot API client (stdlib only). Webhook mode: Telegram calls our
view; we reply by POSTing back to api.telegram.org (whitelisted on PythonAnywhere).
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

API_ROOT = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def _call(token: str, method: str, params: dict, timeout: int = 30) -> dict:
    url = f"{API_ROOT}/bot{token}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise TelegramError(f"Telegram HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TelegramError(f"Telegram ulanmadi: {exc}") from exc


def send_message(token, chat_id, text, reply_markup=None, parse_mode="HTML"):
    params = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
              "disable_web_page_preview": True}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return _call(token, "sendMessage", params)


def edit_message_text(token, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text,
              "parse_mode": parse_mode, "disable_web_page_preview": True}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return _call(token, "editMessageText", params)


def answer_callback_query(token, callback_query_id, text=None):
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text
    return _call(token, "answerCallbackQuery", params)


def set_webhook(token, url, secret_token=None):
    params = {"url": url, "allowed_updates": ["message", "callback_query"]}
    if secret_token:
        params["secret_token"] = secret_token
    return _call(token, "setWebhook", params)


def delete_webhook(token):
    return _call(token, "deleteWebhook", {})


def get_file_path(token, file_id) -> str:
    res = _call(token, "getFile", {"file_id": file_id})
    if not res.get("ok"):
        raise TelegramError(f"getFile xato: {res}")
    return res["result"]["file_path"]


def download_file(token, file_path, timeout: int = 60) -> bytes:
    url = f"{API_ROOT}/file/bot{token}/{file_path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.URLError as exc:
        raise TelegramError(f"Faylni yuklab bo'lmadi: {exc}") from exc


# --- inline keyboard helpers ------------------------------------------------
def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """rows: list of rows; each button is (text, callback_data)."""
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for (text, data) in row]
            for row in rows
        ]
    }
