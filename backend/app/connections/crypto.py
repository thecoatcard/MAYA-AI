"""Authenticated encryption (AES-256-GCM) for stored connection credentials.

The key comes from ``MAYA_CONNECTIONS_ENCRYPTION_KEY`` (32 random bytes, base64). Every value gets
a fresh random 96-bit nonce and is bound to its owner through the associated data
(``workspace:user:provider``), so a stored value copied to another user's row or provider does not
decrypt. Without a key nothing can be stored: the cipher is absent and callers fail closed.
Nothing here logs, and no error message contains key or plaintext material.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = "v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12


class CredentialDecryptError(Exception):
    """A stored value cannot be read (wrong key, tampered, wrong owner or unknown format)."""


class ConnectionsNotConfigured(Exception):
    """``MAYA_CONNECTIONS_ENCRYPTION_KEY`` is not set, so credentials cannot be stored."""


def parse_key(value: str) -> bytes:
    """The 32 key bytes from a base64 (standard or URL-safe) string, else ``ValueError``."""
    text = value.strip()
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != _KEY_BYTES:
        raise ValueError(
            "MAYA_CONNECTIONS_ENCRYPTION_KEY must be 32 random bytes, base64 encoded "
            '(generate one with: python -c "import secrets,base64;'
            'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")'
        )
    return raw


class ConnectionCipher:
    """Encrypts and decrypts one credential blob for one owner."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise ValueError("the key must be 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_setting(cls, value: str | None) -> ConnectionCipher | None:
        return cls(parse_key(value)) if value and value.strip() else None

    @staticmethod
    def aad(workspace_id: object, user_id: object, provider: str) -> bytes:
        return f"maya-connection|{workspace_id}|{user_id}|{provider}".encode()

    def encrypt(self, plaintext: str, aad: bytes) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return f"{_VERSION}.{base64.urlsafe_b64encode(nonce + sealed).decode('ascii')}"

    def decrypt(self, token: str, aad: bytes) -> str:
        version, _, body = token.partition(".")
        if version != _VERSION or not body:
            raise CredentialDecryptError("unknown credential format")
        try:
            blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            if len(blob) <= _NONCE_BYTES:
                raise CredentialDecryptError("credential is truncated")
            return self._aead.decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], aad).decode("utf-8")
        except (InvalidTag, binascii.Error, ValueError):
            # No chained cause: nothing derived from the ciphertext or key may surface.
            raise CredentialDecryptError("credential cannot be decrypted") from None
