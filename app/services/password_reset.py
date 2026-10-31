"""Logique des jetons de réinitialisation de mot de passe."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.password_reset import PasswordResetToken

TOKEN_TTL = timedelta(hours=1)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_token(db: AsyncSession, scope: str, subject_id: int) -> str:
    """Crée un jeton et renvoie sa valeur EN CLAIR (à mettre dans le lien email)."""
    token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        scope=scope,
        subject_id=subject_id,
        token_hash=_hash(token),
        expires_at=utcnow() + TOKEN_TTL,
    )
    db.add(row)
    await db.flush()
    return token


async def verify_token(db: AsyncSession, scope: str, token: str) -> PasswordResetToken | None:
    """Renvoie le jeton s'il est valide (bon scope, non utilisé, non expiré)."""
    row = await db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == _hash(token),
            PasswordResetToken.scope == scope,
        )
    )
    if not row or row.used_at is not None:
        return None
    if row.expires_at < utcnow():
        return None
    return row


def consume(row: PasswordResetToken) -> None:
    row.used_at = utcnow()
