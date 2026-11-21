"""Password hashing and session tokens (standard library only).

* Passwords: PBKDF2-HMAC-SHA256 with a random 16-byte salt per user. The iteration
  count is stored inside the hash string, so it can be raised later without breaking
  existing accounts.
* Sessions: a random 256-bit token is given to the client; only its SHA-256 is stored
  in the database, so a leaked database cannot be used to log in.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

ITERATIONS = 240_000
SESSION_TTL_SECONDS = 7 * 24 * 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
