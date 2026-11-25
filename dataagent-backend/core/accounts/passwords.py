"""Password hashing and verification (salted Argon2id)."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password using salted Argon2id."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return _HASHER.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Whether ``password`` matches ``stored_hash``; an empty hash (no password) never matches."""
    if not isinstance(password, str) or not password or not isinstance(stored_hash, str) or not stored_hash:
        return False
    try:
        return _HASHER.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
