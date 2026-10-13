"""
Passwords as they are stored: hashed with scrypt (standard library), each with its own
salt, in one text that also holds the parameters it was hashed with, so they can be
raised later without losing the passwords hashed before. Pure: no I/O.

A hash costs tens of milliseconds on purpose; call these off the event loop.
"""
import hashlib
import hmac
import secrets

_SCHEME = "scrypt"
# Cost of one hash: 2^14 iterations over 8 blocks of memory, about 16 MB and 50 ms.
_N, _R, _P = 2 ** 14, 8, 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_BYTES)


def hash_password(password: str) -> str:
    """The password as it is stored: `scrypt$n$r$p$salt$key`, salt and key in hex."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, _N, _R, _P)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True when `stored` is the hash of this password, with the parameters it holds.
    A text that is no such hash verifies nothing, and raises nothing."""
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != _SCHEME:
            return False
        derived = _derive(password, bytes.fromhex(salt), int(n), int(r), int(p))
        return hmac.compare_digest(derived, bytes.fromhex(key))
    except (ValueError, TypeError, MemoryError):
        return False


# Verified against when the user name is unknown, so that a wrong name takes as long
# as a wrong password.
NOBODY = hash_password(secrets.token_urlsafe(16))
