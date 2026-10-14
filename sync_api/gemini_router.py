"""
Rotating Gemini caller: multiple API keys × multiple models with rate-limit
failover.

Candidate order is MODEL-MAJOR — every key is tried on the preferred model
before dropping to the next model:

    models = [flash-latest, flash-lite-latest], keys = [k1, k2]
      -> [k1·flash, k2·flash, k1·lite, k2·lite]

When a (key, model) pair returns HTTP 429, it is parked in a DB-backed cooldown
(``GeminiQuota``) — using the 429's ``retryDelay`` when present, until next UTC
midnight for per-day quotas, else 60s — and the next candidate is tried. State
persists across requests, workers and reloads.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from .gemini import GeminiError, GeminiRateLimitError
from .models import GeminiQuota

_PER_MINUTE_DEFAULT = 60.0
_MAX_COOLDOWN = 26 * 3600.0  # safety cap


def _next_utc_midnight(now: float) -> float:
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    nxt = (dt + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return nxt.timestamp()


def _cooldown_until(exc: GeminiRateLimitError, now: float) -> float:
    if exc.retry_seconds:
        return now + min(exc.retry_seconds + 2.0, _MAX_COOLDOWN)
    if exc.is_daily:
        return _next_utc_midnight(now)
    return now + _PER_MINUTE_DEFAULT


def _park(provider_id: str, until: float, err: str) -> None:
    GeminiQuota.objects.update_or_create(
        provider_id=provider_id,
        defaults={"cooldown_until": until, "last_error": err[:200]},
    )


def _is_available(provider_id: str, now: float) -> bool:
    row = GeminiQuota.objects.filter(provider_id=provider_id).first()
    return not row or row.cooldown_until <= now


def build_candidates(keys: list[str], models: list[str]) -> list[tuple[str, str, str]]:
    """Return [(provider_id, key, model)] in model-major order."""
    out = []
    for model in models:
        for idx, key in enumerate(keys):
            if key:
                out.append((f"{idx}:{model}", key, model))
    return out


def call_with_rotation(keys: list[str], models: list[str], fn):
    """Try fn(key, model) across candidates until one succeeds.

    - 429  -> park that pair in cooldown, try next.
    - other GeminiError -> remember, try next (could be a transient/key issue).
    Raises GeminiError if every candidate is cooling down or failing.
    """
    candidates = build_candidates(keys, models)
    if not candidates:
        raise GeminiError("Hech qanday Gemini API key sozlanmagan (.env).")

    now = time.time()
    last_err: Exception | None = None
    skipped = 0

    for provider_id, key, model in candidates:
        if not _is_available(provider_id, now):
            skipped += 1
            continue
        try:
            result = fn(key, model)
            # success: clear any stale cooldown for this pair
            GeminiQuota.objects.filter(provider_id=provider_id).update(
                cooldown_until=0.0, last_error=""
            )
            return result
        except GeminiRateLimitError as exc:
            until = _cooldown_until(exc, now)
            _park(provider_id, until, str(exc))
            last_err = exc
        except GeminiError as exc:
            # transient / model error: brief cooldown so we rotate off it
            _park(provider_id, now + _PER_MINUTE_DEFAULT, str(exc))
            last_err = exc

    if last_err:
        raise GeminiError(f"Barcha Gemini kalit/modellar band yoki xato: {last_err}")
    raise GeminiError(
        f"Barcha Gemini kalit/modellar hozir limitда ({skipped} ta kutmoqda). "
        "Birozdan keyin qayta urinib ko'ring."
    )
