"""Password hashing and verification (Argon2id primary, legacy bcrypt upgrade)."""
from __future__ import annotations

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password using salted Argon2id."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return _HASHER.hash(password)


def verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    """Verify ``password`` against ``stored_hash``.

    Returns ``(matched, needs_rehash)``. Legacy bcrypt hashes (imported during
    cutover) return ``(True, True)`` on success so callers transparently upgrade
    the stored hash to Argon2id.
    """
    if not isinstance(password, str) or not password or not isinstance(stored_hash, str) or not stored_hash:
        return False, False

    if stored_hash.startswith("$2"):
        try:
            matched = bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        except ValueError:
            # bcrypt 5 raises ValueError on >72 byte inputs; treat as mismatch.
            return False, False
        return (True, True) if matched else (False, False)

    if stored_hash.startswith("$argon2"):
        try:
            _HASHER.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, False
        return True, _HASHER.check_needs_rehash(stored_hash)

    return False, False
