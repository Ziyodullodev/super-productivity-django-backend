"""
Minimal Gemini REST client (stdlib only) for turning a voice note (or text) into
a structured task: {title, project, due_today}. Uses the Generative Language API
``generateContent`` with inline audio; no SDK needed (works on PythonAnywhere's
whitelisted ``.googleapis.com``).
"""
from __future__ import annotations

import base64
import json
import urllib.request
import urllib.error

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

_SYSTEM = (
    "You turn a user's note (spoken or typed, often in Uzbek or Russian) into a "
    "single actionable to-do task. Reply with ONLY a JSON object, no markdown. "
    "Fields: 'title' (short imperative task text in the user's language), "
    "'project' (the single best-matching project name from the provided list, or "
    "null if none clearly fits), 'due_today' (true if the note implies today/now/"
    "'bugun', else false). Keep the title concise."
)


class GeminiError(Exception):
    pass


class GeminiRateLimitError(GeminiError):
    """HTTP 429 (quota exceeded). Carries a cooldown hint for the router."""

    def __init__(self, message: str, retry_seconds: float | None = None, is_daily: bool = False):
        super().__init__(message)
        self.retry_seconds = retry_seconds
        self.is_daily = is_daily


def _parse_429(detail: str) -> tuple[float | None, bool]:
    """Extract retryDelay seconds and whether it's a per-day quota from a 429 body."""
    retry_seconds = None
    is_daily = "PerDay" in detail or "per day" in detail.lower()
    try:
        data = json.loads(detail)
        for d in (data.get("error", {}).get("details") or []):
            rd = d.get("retryDelay")
            if isinstance(rd, str) and rd.endswith("s"):
                retry_seconds = float(rd[:-1])
            quota_id = str(d.get("violations", d)) + str(d)
            if "PerDay" in quota_id:
                is_daily = True
    except (ValueError, TypeError, AttributeError):
        pass
    return retry_seconds, is_daily


def _post(model: str, api_key: str, parts: list, timeout: int = 60) -> dict:
    url = f"{API_ROOT}/{model}:generateContent?key={api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:600]
        if exc.code == 429:
            retry_seconds, is_daily = _parse_429(detail)
            raise GeminiRateLimitError(
                f"Gemini 429 ({model}): quota", retry_seconds, is_daily
            ) from exc
        raise GeminiError(f"Gemini HTTP {exc.code} ({model}): {detail[:200]}") from exc
    except urllib.error.URLError as exc:
        raise GeminiError(f"Gemini ulanmadi: {exc}") from exc

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise GeminiError(f"Gemini kutilmagan javob: {str(payload)[:200]}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Gemini JSON emas: {text[:200]}") from exc
    return _normalize(data)


def _normalize(data: dict) -> dict:
    title = (data.get("title") or "").strip()
    project = data.get("project")
    if isinstance(project, str):
        project = project.strip() or None
    return {
        "title": title,
        "project": project,
        "due_today": bool(data.get("due_today")),
    }


def _project_hint(project_names: list[str]) -> str:
    names = ", ".join(project_names) if project_names else "(none)"
    return f"Available projects: {names}."


def transcribe_voice(
    audio_bytes: bytes, mime_type: str, project_names: list[str], api_key: str, model: str
) -> dict:
    parts = [
        {"text": _project_hint(project_names) + " Extract the task from this audio."},
        {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(audio_bytes).decode("ascii")}},
    ]
    return _post(model, api_key, parts)


def parse_text(text: str, project_names: list[str], api_key: str, model: str) -> dict:
    parts = [{"text": _project_hint(project_names) + f" The note is: {text}"}]
    return _post(model, api_key, parts)
