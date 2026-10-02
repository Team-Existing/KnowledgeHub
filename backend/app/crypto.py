"""
Application-layer encryption for stored credentials (connector tokens, API keys).

AES-256-GCM. Every value is bound to where it is stored (`context`, e.g.
"connector:<id>:api_token") as associated data, so a ciphertext copied into
another connector or field fails to decrypt instead of leaking the secret
there. Stored form:

    enc:v1:<key id>:<base64url(nonce || ciphertext+tag)>

Keys come from CREDENTIALS_KEY: one or more base64url-encoded 32-byte keys,
comma-separated. The first encrypts; the others only decrypt, which is how a
key is rotated (prepend the new key, restart — startup re-encrypts everything
with it — then drop the old one). Without CREDENTIALS_KEY nothing is
encrypted and storing a credential is refused: there is no plaintext fallback.

Generate a key:  python -m app.crypto
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"
_NONCE_BYTES = 12


class CredentialsKeyMissing(RuntimeError):
    pass


class DecryptionError(RuntimeError):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        raise CredentialsKeyMissing("CREDENTIALS_KEY is not valid base64url")
    if len(key) != 32:
        raise CredentialsKeyMissing("CREDENTIALS_KEY must decode to 32 bytes (run: python -m app.crypto)")
    return key


def _key_id(key: bytes) -> str:
    return hashlib.sha256(b"kh-credentials-key:" + key).hexdigest()[:8]


def _keys() -> List[Tuple[str, bytes]]:
    """[(key id, key)], current key first. Read on every call so tests and rotations see changes."""
    raw = [k.strip() for k in os.getenv("CREDENTIALS_KEY", "").split(",") if k.strip()]
    return [(_key_id(k), k) for k in map(_decode_key, raw)]


def available() -> bool:
    try:
        return bool(_keys())
    except CredentialsKeyMissing:
        return False


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext: str, context: str) -> str:
    keys = _keys()
    if not keys:
        raise CredentialsKeyMissing(
            "Storing credentials needs CREDENTIALS_KEY to be set on the server (generate one with: python -m app.crypto)"
        )
    kid, key = keys[0]
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode(), context.encode())
    return f"{PREFIX}{kid}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"


def decrypt(stored: str, context: str) -> str:
    if not is_encrypted(stored):
        raise DecryptionError("value is not encrypted")
    try:
        kid, payload = stored[len(PREFIX):].split(":", 1)
        blob = base64.urlsafe_b64decode(payload)
    except (ValueError, TypeError):
        raise DecryptionError("malformed encrypted value")
    key = dict(_keys()).get(kid)
    if key is None:
        raise DecryptionError(f"encrypted with key {kid}, which is not in CREDENTIALS_KEY")
    try:
        return AESGCM(key).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], context.encode()).decode()
    except InvalidTag:
        raise DecryptionError("credential failed authentication (wrong key, tampered, or moved from another record)")


def needs_reencryption(stored: str) -> bool:
    """True for plaintext, or ciphertext under a key that is no longer the current one."""
    if not is_encrypted(stored):
        return True
    keys = _keys()
    return bool(keys) and not stored[len(PREFIX):].startswith(keys[0][0] + ":")


def seal_fields(values: Dict[str, Optional[str]], names: List[str], context_prefix: str) -> Dict[str, Optional[str]]:
    """Encrypt the named fields (plaintext ones only) of a config dict."""
    out = dict(values)
    for name in names:
        value = out.get(name)
        if value and not is_encrypted(value):
            out[name] = encrypt(str(value), f"{context_prefix}:{name}")
    return out


def open_fields(values: Dict[str, Optional[str]], names: List[str], context_prefix: str) -> Dict[str, Optional[str]]:
    out = dict(values)
    for name in names:
        value = out.get(name)
        if value:
            out[name] = decrypt(str(value), f"{context_prefix}:{name}")
    return out


if __name__ == "__main__":
    print(generate_key())
