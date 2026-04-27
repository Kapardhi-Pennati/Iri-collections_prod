"""
AES-256-GCM encryption utilities and encrypted model fields for PII-at-rest.

Design goals:
- Backward compatible with legacy plaintext rows.
- Authenticated encryption (AES-GCM) with random nonce per value.
- Simple field wrapper for Django models.
"""

import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

ENC_PREFIX = "encv1:"


def _get_encryption_key() -> bytes:
    raw = getattr(settings, "PII_ENCRYPTION_KEY", "")
    if not raw:
        raise ImproperlyConfigured("PII_ENCRYPTION_KEY must be configured.")

    try:
        key = base64.urlsafe_b64decode(raw.encode("utf-8"))
    except Exception as exc:
        raise ImproperlyConfigured("PII_ENCRYPTION_KEY is not valid base64.") from exc

    if len(key) != 32:
        raise ImproperlyConfigured("PII_ENCRYPTION_KEY must decode to 32 bytes (AES-256).")
    return key


def pii_hash(value: Optional[str]) -> str:
    """Deterministic hash for equality matching without storing plaintext."""
    normalized = (value or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def encrypt_text(plaintext: str) -> str:
    if plaintext is None:
        return ""

    text = str(plaintext)
    if text == "":
        return ""

    if text.startswith(ENC_PREFIX):
        return text

    key = _get_encryption_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, text.encode("utf-8"), None)
    packed = base64.urlsafe_b64encode(nonce + ciphertext).decode("utf-8")
    return f"{ENC_PREFIX}{packed}"


def decrypt_text(stored_value: str) -> str:
    if stored_value is None:
        return ""

    value = str(stored_value)
    if value == "":
        return ""

    # Backward compatibility: treat non-prefixed values as legacy plaintext.
    if not value.startswith(ENC_PREFIX):
        return value

    encoded = value[len(ENC_PREFIX):]
    raw = base64.urlsafe_b64decode(encoded.encode("utf-8"))
    nonce, ciphertext = raw[:12], raw[12:]

    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


class EncryptedTextField(models.TextField):
    """Stores encrypted text in DB and returns plaintext in Python."""

    def get_prep_value(self, value):
        if value is None:
            return value
        return encrypt_text(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt_text(value)

    def to_python(self, value):
        if value is None or isinstance(value, str) and not value.startswith(ENC_PREFIX):
            return value
        if isinstance(value, str):
            return decrypt_text(value)
        return value


class EncryptedCharField(EncryptedTextField):
    """
    Char-like encrypted field backed by TextField to avoid ciphertext truncation.
    """

    def __init__(self, *args, **kwargs):
        kwargs.pop("max_length", None)
        super().__init__(*args, **kwargs)
