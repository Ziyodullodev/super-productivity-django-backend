"""
Read and mutate a Super Productivity v2 ``sync-data.json`` envelope stored in the
``SyncFile`` table, so the Telegram bot can add / complete / delete / move tasks.

How the app accepts our change (see docs/sync-and-op-log + the Explore report):
we append a compact operation to ``recentOps`` AND mirror the change into the
``state`` snapshot, then bump ``syncVersion`` (+1) and the ``vectorClock``
(increment our own clientId so the clock strictly dominates the last-seen one).
On the user's device the op is REPLAYED through the app's own meta-reducer, so a
correct op payload makes the app perform the mutation itself — no conflict dialog.

Op/action facts verified from source:
  addTask   -> code 'HA', opType 'CRT', payload {task, workContextId,
               workContextType:'PROJECT', isAddToBacklog, isAddToBottom}
  updateTask-> code 'HU', opType 'UPD', payload {task:{id, changes}}
  deleteTask-> code 'HD', opType 'DEL', payload {task: TaskWithSubTasks}
  moveToOtherProject -> code 'HMP', opType 'UPD',
               payload {task: TaskWithSubTasks, targetProjectId}
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

from .models import SyncFile
from . import sp_crypto

TODAY_TAG_ID = "TODAY"  # virtual tag; membership is by dueDay, taskIds only orders


class SpSyncError(Exception):
    pass


# --- ids & time -------------------------------------------------------------
def uuid7() -> str:
    """Time-ordered UUIDv7 (ms timestamp + random), matching the app's op ids."""
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(ms.to_bytes(6, "big") + rand)
    b[6] = 0x70 | (b[6] & 0x0F)          # version 7
    b[8] = 0x80 | (b[8] & 0x3F)          # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(b)))


def now_ms() -> int:
    return int(time.time() * 1000)


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --- envelope load / save ---------------------------------------------------
def load_envelope(username: str, path: str, password: str | None):
    """Return (SyncFile, envelope_dict, flags). Raises SpSyncError on problems."""
    try:
        sf = SyncFile.objects.get(user__username=username, path=path, is_directory=False)
    except SyncFile.DoesNotExist as exc:
        raise SpSyncError(
            f"Sync fayli topilmadi: {path}. Ilovadan bir marta sync qiling."
        ) from exc
    if not sf.content:
        raise SpSyncError("Sync fayli bo'sh.")
    data_str = bytes(sf.content).decode("utf-8", errors="strict")
    try:
        envelope, flags = sp_crypto.decode_sync_file(data_str, password)
    except sp_crypto.SyncFormatError as exc:
        raise SpSyncError(f"Sync faylini o'qib bo'lmadi: {exc}") from exc
    if envelope.get("version") != 2:
        raise SpSyncError(
            "Sync fayli v2 formatida emas (ehtimol 'split sync' yoqilgan) — "
            "bot hozircha faqat standart v2 formatini qo'llab-quvvatlaydi."
        )
    return sf, envelope, flags


def save_envelope(sf: SyncFile, envelope: dict, flags, password: str | None) -> None:
    data_str = sp_crypto.encode_sync_file(envelope, flags, password)
    sf.update_content(data_str.encode("utf-8"))


# --- projects & tasks (read) ------------------------------------------------
def _state(envelope: dict) -> dict:
    st = envelope.get("state")
    if not isinstance(st, dict):
        raise SpSyncError("Sync faylida 'state' yo'q.")
    return st


def list_projects(envelope: dict) -> list[dict]:
    """Non-archived projects as [{id, title}] in stored order."""
    proj = _state(envelope).get("project", {})
    ids = proj.get("ids", [])
    ents = proj.get("entities", {})
    out = []
    for pid in ids:
        p = ents.get(pid)
        if p and not p.get("isArchived") and not p.get("isHiddenFromMenu"):
            out.append({"id": pid, "title": p.get("title", pid)})
    return out


def resolve_project_id(envelope: dict, suggested_name: str | None) -> tuple[str, str]:
    """Map a Gemini-suggested name to a project id. Fall back to Inbox / first."""
    projects = list_projects(envelope)
    if not projects:
        raise SpSyncError("Hech qanday project topilmadi.")
    if suggested_name:
        low = suggested_name.strip().lower()
        for p in projects:
            if p["title"].strip().lower() == low:
                return p["id"], p["title"]
        for p in projects:
            if low and (low in p["title"].lower() or p["title"].lower() in low):
                return p["id"], p["title"]
    for p in projects:
        if p["title"].strip().lower() in ("inbox", "inbox project"):
            return p["id"], p["title"]
    return projects[0]["id"], projects[0]["title"]


def list_today_tasks(envelope: dict, limit: int = 20) -> list[dict]:
    st = _state(envelope)
    ents = st.get("task", {}).get("entities", {})
    today = today_str()
    out = []
    for tid, t in ents.items():
        if not t:
            continue
        due_day = t.get("dueDay")
        if due_day == today and not t.get("isDone"):
            out.append({"id": tid, "title": t.get("title", ""), "isDone": bool(t.get("isDone"))})
    return out[:limit]


def task_with_subtasks(envelope: dict, task_id: str) -> dict:
    ents = _state(envelope).get("task", {}).get("entities", {})
    t = ents.get(task_id)
    if not t:
        raise SpSyncError("Task topilmadi.")
    subs = [ents[s] for s in t.get("subTaskIds", []) if ents.get(s)]
    return {**t, "subTasks": subs}


# --- envelope mutation core -------------------------------------------------
def _bump_and_append(envelope: dict, op: dict, client_id: str) -> None:
    clock = dict(envelope.get("vectorClock") or {})
    clock[client_id] = clock.get(client_id, 0) + 1
    new_sync_version = int(envelope.get("syncVersion", 0)) + 1

    op["c"] = client_id
    op["v"] = clock
    op["t"] = now_ms()
    op["s"] = int(envelope.get("schemaVersion", 0))
    op["sv"] = new_sync_version

    recent = list(envelope.get("recentOps") or [])
    recent.append(op)
    MAX = 2000
    if len(recent) > MAX:
        recent = recent[-MAX:]
    envelope["recentOps"] = recent
    if not envelope.get("oldestOpSyncVersion"):
        envelope["oldestOpSyncVersion"] = recent[0].get("sv", new_sync_version)

    envelope["syncVersion"] = new_sync_version
    envelope["vectorClock"] = clock
    envelope["clientId"] = client_id
    envelope["lastModified"] = now_ms()


def _make_task(title: str, project_id: str, due_today: bool) -> dict:
    task = {
        "id": uuid7(),
        "title": title.strip(),
        "projectId": project_id,
        "isDone": False,
        "subTaskIds": [],
        "timeSpentOnDay": {},
        "timeSpent": 0,
        "timeEstimate": 0,
        "tagIds": [],
        "created": now_ms(),
        "attachments": [],
    }
    if due_today:
        task["dueDay"] = today_str()
    return task


def add_task(envelope, title, project_id, client_id, due_today=False) -> dict:
    task = _make_task(title, project_id, due_today)
    op = {
        "id": uuid7(), "a": "HA", "o": "CRT", "e": "TASK", "d": task["id"],
        "p": {
            "task": task,
            "workContextId": project_id,
            "workContextType": "PROJECT",
            "isAddToBacklog": False,
            "isAddToBottom": True,
        },
    }
    _bump_and_append(envelope, op, client_id)

    # mirror into state snapshot
    st = _state(envelope)
    tstate = st.setdefault("task", {"ids": [], "entities": {}})
    tstate.setdefault("entities", {})[task["id"]] = task
    tstate.setdefault("ids", []).append(task["id"])
    proj = st.get("project", {}).get("entities", {}).get(project_id)
    if proj is not None:
        proj.setdefault("taskIds", []).append(task["id"])
    if due_today:
        tag = st.get("tag", {}).get("entities", {}).get(TODAY_TAG_ID)
        if tag is not None:
            tag.setdefault("taskIds", []).append(task["id"])
    return task


def set_task_done(envelope, task_id, done, client_id) -> None:
    changes = {"isDone": bool(done)}
    changes["doneOn"] = now_ms() if done else None
    op = {
        "id": uuid7(), "a": "HU", "o": "UPD", "e": "TASK", "d": task_id,
        "p": {"task": {"id": task_id, "changes": changes}},
    }
    _bump_and_append(envelope, op, client_id)
    ent = _state(envelope).get("task", {}).get("entities", {}).get(task_id)
    if ent is not None:
        ent["isDone"] = bool(done)
        ent["doneOn"] = changes["doneOn"]


def delete_task(envelope, task_id, client_id) -> None:
    twst = task_with_subtasks(envelope, task_id)
    op = {
        "id": uuid7(), "a": "HD", "o": "DEL", "e": "TASK", "d": task_id,
        "p": {"task": twst},
    }
    _bump_and_append(envelope, op, client_id)
    st = _state(envelope)
    tstate = st.get("task", {})
    ents = tstate.get("entities", {})
    remove_ids = [task_id] + list(twst.get("subTaskIds", []))
    for rid in remove_ids:
        ents.pop(rid, None)
    tstate["ids"] = [i for i in tstate.get("ids", []) if i not in remove_ids]
    for p in st.get("project", {}).get("entities", {}).values():
        if isinstance(p, dict):
            for key in ("taskIds", "backlogTaskIds"):
                if key in p:
                    p[key] = [i for i in p[key] if i not in remove_ids]
    for t in st.get("tag", {}).get("entities", {}).values():
        if isinstance(t, dict) and "taskIds" in t:
            t["taskIds"] = [i for i in t["taskIds"] if i not in remove_ids]


def move_task(envelope, task_id, target_project_id, client_id) -> None:
    twst = task_with_subtasks(envelope, task_id)
    op = {
        "id": uuid7(), "a": "HMP", "o": "UPD", "e": "TASK", "d": task_id,
        "p": {"task": twst, "targetProjectId": target_project_id},
    }
    _bump_and_append(envelope, op, client_id)
    st = _state(envelope)
    ents = st.get("task", {}).get("entities", {})
    move_ids = [task_id] + list(twst.get("subTaskIds", []))
    for p in st.get("project", {}).get("entities", {}).values():
        if isinstance(p, dict):
            for key in ("taskIds", "backlogTaskIds"):
                if key in p:
                    p[key] = [i for i in p[key] if i not in move_ids]
    for mid in move_ids:
        if ents.get(mid) is not None:
            ents[mid]["projectId"] = target_project_id
    tgt = st.get("project", {}).get("entities", {}).get(target_project_id)
    if tgt is not None:
        tgt.setdefault("taskIds", []).extend(move_ids)
