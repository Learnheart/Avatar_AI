"""Passwords as they are stored."""
import pytest

from backend.features.accounts.passwords import NOBODY, hash_password, verify_password


def test_a_hash_verifies_its_password_and_no_other():
    stored = hash_password("mật khẩu của tôi")

    assert verify_password("mật khẩu của tôi", stored)
    assert not verify_password("mật khẩu của tôi ", stored)
    assert not verify_password("", stored)


def test_the_password_is_not_in_its_hash_and_two_hashes_differ():
    first, second = hash_password("correct horse"), hash_password("correct horse")

    assert "correct horse" not in first
    assert first != second
    assert verify_password("correct horse", first) and verify_password("correct horse", second)


def test_a_hash_holds_the_parameters_it_is_verified_with():
    scheme, n, r, p, salt, key = hash_password("x" * 8).split("$")

    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(key)) == 32


def test_a_hash_made_with_other_parameters_still_verifies():
    import hashlib

    salt = bytes(16)
    key = hashlib.scrypt(b"old password", salt=salt, n=1024, r=8, p=1, dklen=32)
    stored = f"scrypt$1024$8$1${salt.hex()}${key.hex()}"

    assert verify_password("old password", stored)
    assert not verify_password("new password", stored)


@pytest.mark.parametrize(
    "stored",
    ["", "plain text", "scrypt$1$2", "md5$16384$8$1$00$00", "scrypt$x$8$1$00$00",
     "scrypt$16384$8$1$zz$00", "scrypt$3$8$1$00$00"],
)
def test_a_text_that_is_no_hash_verifies_nothing_and_raises_nothing(stored):
    assert verify_password("anything", stored) is False


def test_nobody_s_hash_is_a_real_hash_no_password_opens():
    assert NOBODY.startswith("scrypt$")
    assert not verify_password("", NOBODY)
