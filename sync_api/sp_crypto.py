"""
Super Productivity sync-file encoding/decoding — a byte-exact Python port of the
app's wire format so the Telegram bot can read and rewrite ``sync-data.json``.

Reference (this repo):
  - packages/sync-core/src/sync-file-prefix.ts   (prefix: ``pf_[C][E]<ver>__``)
  - packages/sync-core/src/encryption/web-crypto.ts (AES-256-GCM, 16B salt, 12B iv)
  - packages/sync-core/src/encryption/argon2.ts  (Argon2id p=1 t=3 m=65536 KiB, 32B key)
  - packages/sync-core/src/encryption.ts         (wire: base64([salt16][iv12][ct+tag]))
  - compression: gzip, then base64; order is compress-THEN-encrypt.

Only the current Argon2id format is produced on write. Legacy PBKDF2 ([iv12][ct])
is decode-only (back-compat), matching the app.
"""
from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- constants (mirror web-crypto.ts / argon2.ts) ---------------------------
PREFIX = "pf_"
END_SEP = "__"
MODEL_VERSION = 2

SALT_LEN = 16
IV_LEN = 12
KEY_LEN = 32
GCM_TAG_LEN = 16

ARGON2_TIME_COST = 3
ARGON2_MEMORY_KIB = 65536  # 64 MB
ARGON2_PARALLELISM = 1

_MIN_ARGON2 = SALT_LEN + IV_LEN + GCM_TAG_LEN  # 44
_MIN_LEGACY = IV_LEN + GCM_TAG_LEN  # 28


class SyncFormatError(Exception):
    """Raised when a sync file prefix/body cannot be parsed."""


@dataclass
class ParsedFlags:
    is_compressed: bool
    is_encrypted: bool
    model_version: float


def _derive_key(password: str, salt: bytes) -> bytes:
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_KIB,
        parallelism=ARGON2_PARALLELISM,
        hash_len=KEY_LEN,
        type=Type.ID,
    )


def _decrypt_body(b64_body: str, password: str) -> str:
    raw = base64.b64decode(b64_body)
    if len(raw) >= _MIN_ARGON2:
        salt, iv, ct = raw[:SALT_LEN], raw[SALT_LEN : SALT_LEN + IV_LEN], raw[SALT_LEN + IV_LEN :]
        key = _derive_key(password, salt)
        return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
    if len(raw) >= _MIN_LEGACY:
        # Legacy PBKDF2 (decode-only): key = PBKDF2(pw, salt=pw, 1000, sha256, 32)
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        iv, ct = raw[:IV_LEN], raw[IV_LEN:]
        key = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=KEY_LEN,
            salt=password.encode("utf-8"), iterations=1000,
        ).derive(password.encode("utf-8"))
        return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
    raise SyncFormatError("ciphertext too short to be valid")


def _encrypt_body(plaintext: str, password: str) -> str:
    import os

    salt = os.urandom(SALT_LEN)
    iv = os.urandom(IV_LEN)
    key = _derive_key(password, salt)
    ct = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
    return base64.b64encode(salt + iv + ct).decode("ascii")


def parse_prefix(data_str: str) -> ParsedFlags:
    """Parse the ``pf_[C][E]<ver>__`` prefix. Mirrors extractSyncFileStateFromPrefix."""
    if not data_str.startswith(PREFIX):
        raise SyncFormatError("missing sync file prefix")
    rest = data_str[len(PREFIX):]
    is_compressed = rest.startswith("C")
    if is_compressed:
        rest = rest[1:]
    is_encrypted = rest.startswith("E")
    if is_encrypted:
        rest = rest[1:]
    sep = rest.find(END_SEP)
    if sep < 0:
        raise SyncFormatError("missing prefix end separator")
    try:
        version = float(rest[:sep])
    except ValueError as exc:
        raise SyncFormatError("invalid model version in prefix") from exc
    return ParsedFlags(is_compressed, is_encrypted, version)


def decode_sync_file(data_str: str, password: str | None = None) -> tuple[dict, ParsedFlags]:
    """Decode a stored sync file body into the envelope dict + its original flags."""
    flags = parse_prefix(data_str)
    body = data_str[data_str.find(END_SEP) + len(END_SEP):]
    if flags.is_encrypted:
        if not password:
            raise SyncFormatError("file is encrypted but no password was provided")
        body = _decrypt_body(body, password)
    if flags.is_compressed:
        body = gzip.decompress(base64.b64decode(body)).decode("utf-8")
    return json.loads(body), flags


def encode_sync_file(envelope: dict, flags: ParsedFlags, password: str | None = None) -> str:
    """Re-encode an envelope using the SAME flags the file was read with."""
    body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
    if flags.is_compressed:
        body = base64.b64encode(gzip.compress(body.encode("utf-8"))).decode("ascii")
    if flags.is_encrypted:
        if not password:
            raise SyncFormatError("file is encrypted but no password was provided")
        body = _encrypt_body(body, password)
    c = "C" if flags.is_compressed else ""
    e = "E" if flags.is_encrypted else ""
    prefix = f"{PREFIX}{c}{e}{MODEL_VERSION}{END_SEP}"
    return prefix + body
