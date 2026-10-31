"""Tests de la détection de configuration (SMTP, secrets)."""

from app.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        smtp_host="smtp.example.com",
        smtp_user="real@example.com",
        smtp_password="real-password",
        secret_key="a-very-long-random-secret-value-1234567890",
        jwt_secret="another-long-random-secret-value-0987654321",
    )
    base.update(overrides)
    return Settings(**base)


def test_smtp_configured_true_with_real_values():
    assert _settings().smtp_configured is True


def test_smtp_configured_false_with_placeholder_user():
    assert _settings(smtp_user="your@email.com").smtp_configured is False


def test_smtp_configured_false_with_empty_password():
    assert _settings(smtp_password="").smtp_configured is False


def test_insecure_secrets_detects_placeholders():
    s = _settings(secret_key="change-me-to-a-random-64-char-string", jwt_secret="change-me-too")
    assert "SECRET_KEY" in s.insecure_secrets
    assert "JWT_SECRET" in s.insecure_secrets


def test_insecure_secrets_empty_when_hardened():
    assert _settings().insecure_secrets == []
