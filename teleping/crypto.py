"""Authenticated encryption for every persisted private payload."""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
        if len(decoded) != 32:
            raise ValueError
        return decoded
    except Exception:
        raise ValueError("encryption_key_invalid") from None


def encrypt(data: dict | list, key: str, *, purpose: str) -> bytes:
    nonce = os.urandom(12)
    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(_key(key)).encrypt(nonce, plaintext, ("teleping:v1:" + purpose).encode())
    return json.dumps({"v": 1, "nonce": base64.urlsafe_b64encode(nonce).decode(), "ciphertext": base64.urlsafe_b64encode(ciphertext).decode()}, separators=(",", ":")).encode()


def decrypt(data: bytes, key: str, *, purpose: str) -> dict | list:
    encryption_key = _key(key)
    try:
        envelope = json.loads(data)
        if envelope["v"] != 1:
            raise ValueError
        nonce = base64.b64decode(envelope["nonce"], altchars=b"-_", validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], altchars=b"-_", validate=True)
        plaintext = AESGCM(encryption_key).decrypt(nonce, ciphertext, ("teleping:v1:" + purpose).encode())
        result = json.loads(plaintext)
        if not isinstance(result, (dict, list)):
            raise ValueError
        return result
    except Exception:
        raise ValueError("encrypted_data_invalid") from None
