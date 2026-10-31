"""Dépendances FastAPI partagées (auth, contexte tenant)."""

from collections.abc import AsyncGenerator

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.client_portal import ClientPortalUser
from app.models.dossier import DossierAccess
from app.models.user import User
from app.security import decode_token

ACTIVE_DOSSIER_COOKIE = "active_dossier"


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async for s in get_db():
        yield s


async def _apply_active_dossier(db: AsyncSession, user: User, active: str | None) -> User:
    """Bascule l'utilisateur sur le dossier actif s'il y a accès.

    On détache l'objet de la session (expunge) avant de changer tenant_id :
    la surcharge reste en mémoire et n'est JAMAIS écrite en base.
    """
    if not active:
        return user
    try:
        active_id = int(active)
    except (TypeError, ValueError):
        return user
    if active_id == user.tenant_id:
        return user
    granted = await db.scalar(
        select(DossierAccess).where(
            DossierAccess.user_id == user.id, DossierAccess.tenant_id == active_id
        )
    )
    if not granted:
        return user  # accès non autorisé → on reste sur le dossier d'origine
    db.expunge(user)
    user.tenant_id = active_id
    return user


async def get_current_user(
    token: str | None = Cookie(default=None, alias="libfad_token"),
    active_dossier: str | None = Cookie(default=None, alias=ACTIVE_DOSSIER_COOKIE),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non authentifié")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token incomplet")

    user = await db.scalar(select(User).where(User.id == int(user_id)))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utilisateur introuvable")
    return await _apply_active_dossier(db, user, active_dossier)


async def get_current_tenant_id(user: User = Depends(get_current_user)) -> int:
    return user.tenant_id


async def get_optional_user(
    token: str | None = Cookie(default=None, alias="libfad_token"),
    active_dossier: str | None = Cookie(default=None, alias=ACTIVE_DOSSIER_COOKIE),
    db: AsyncSession = Depends(get_session),
) -> User | None:
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = await db.scalar(select(User).where(User.id == int(user_id)))
    if not user:
        return None
    return await _apply_active_dossier(db, user, active_dossier)


# --- Auth du portail client -------------------------------------------------
# Cookie et marqueur de token distincts de l'interface entreprise pour qu'un
# client ne puisse jamais accéder à l'espace de gestion, et inversement.
PORTAL_COOKIE = "libfad_portal_token"


async def get_optional_portal_user(
    token: str | None = Cookie(default=None, alias=PORTAL_COOKIE),
    db: AsyncSession = Depends(get_session),
) -> ClientPortalUser | None:
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("typ") != "portal":
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = await db.scalar(
        select(ClientPortalUser)
        .where(ClientPortalUser.id == int(user_id))
        .options(selectinload(ClientPortalUser.client))
    )
    if not user or not user.is_active:
        return None
    return user


async def get_current_portal_user(
    user: ClientPortalUser | None = Depends(get_optional_portal_user),
) -> ClientPortalUser:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non authentifié")
    return user
