"""Tests du hachage de mot de passe (bcrypt) et des tokens."""

from app.security import create_access_token, decode_token, hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("demo1234")
    assert h.startswith("$2b$")  # hash bcrypt
    assert verify_password("demo1234", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("demo1234")
    assert verify_password("mauvais", h) is False


def test_verify_handles_invalid_hash_gracefully():
    # Un hash corrompu ne doit pas lever, juste renvoyer False.
    assert verify_password("x", "pas-un-hash") is False


def test_long_password_does_not_crash():
    # bcrypt limite à 72 octets : on tronque, ça ne doit pas lever.
    long_pw = "a" * 200
    h = hash_password(long_pw)
    assert verify_password(long_pw, h) is True


def test_token_roundtrip():
    token = create_access_token("user@example.com")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user@example.com"


def test_decode_invalid_token_returns_none():
    assert decode_token("not.a.valid.token") is None
