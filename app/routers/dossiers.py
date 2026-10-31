"""Multi-dossiers : liste, bascule et création de dossiers (sociétés)."""

import secrets
import string
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import ACTIVE_DOSSIER_COOKIE, get_current_user, get_session
from app.models.base import utcnow
from app.models.dossier import DossierAccess
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.types import normalize_email
from app.security import hash_password
from app.services.email import send_email

router = APIRouter()


def _gen_password(n: int = 12) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


async def _accessible_tenant_ids(db: AsyncSession, user: User) -> set[int]:
    ids = set(await db.scalars(
        select(DossierAccess.tenant_id).where(DossierAccess.user_id == user.id)
    ))
    ids.add(user.tenant_id)  # le dossier courant est toujours accessible
    return ids


@router.get("/api/dossiers")
async def list_dossiers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    ids = await _accessible_tenant_ids(db, user)
    tenants = list(await db.scalars(
        select(Tenant).where(Tenant.id.in_(ids)).order_by(Tenant.name)
    ))
    current = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    return JSONResponse({
        "current": {"id": user.tenant_id, "name": current.name if current else "—"},
        "dossiers": [{"id": t.id, "name": t.name} for t in tenants],
    })


@router.get("/dossiers/switch/{tenant_id}")
async def switch_dossier(
    tenant_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    ids = await _accessible_tenant_ids(db, user)
    resp = RedirectResponse(url="/", status_code=302)
    if tenant_id in ids:
        resp.set_cookie(ACTIVE_DOSSIER_COOKIE, str(tenant_id), max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax")
    return resp


@router.post("/cabinet/collaborators/invite")
async def invite_collaborator(
    full_name: str = Form(...),
    email: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Invite un collaborateur sur le dossier courant. Crée le compte s'il n'existe
    pas, sinon lui accorde simplement l'accès au dossier courant."""
    email_n = normalize_email(email)
    existing = await db.scalar(select(User).where(User.email == email_n))
    tenant_id = user.tenant_id

    if existing:
        access = await db.scalar(select(DossierAccess).where(
            DossierAccess.user_id == existing.id, DossierAccess.tenant_id == tenant_id))
        if not access:
            db.add(DossierAccess(user_id=existing.id, tenant_id=tenant_id, role="collaborator"))
            await db.commit()
        return {"granted": True, "existing": True, "email": email_n}

    password = _gen_password()
    new = User(
        tenant_id=tenant_id, email=email_n, password_hash=hash_password(password),
        full_name=full_name.strip(), role="collaborator", locale="fr_MA",
    )
    db.add(new)
    await db.flush()
    db.add(DossierAccess(user_id=new.id, tenant_id=tenant_id, role="collaborator"))
    await db.commit()

    login_url = f"{settings.base_url.rstrip('/')}/login"
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    try:
        await send_email(
            to=email_n,
            subject=f"Invitation à collaborer — {tenant.name if tenant else settings.app_name}",
            body_text=(
                f"Bonjour,\n\nVous avez été invité(e) à collaborer sur {settings.app_name}.\n"
                f"Connexion : {login_url}\nEmail : {email_n}\nMot de passe : {password}\n\n"
                f"Cordialement."
            ),
        )
    except Exception:
        pass
    return {"created": True, "email": email_n, "password": password, "login_url": login_url}


@router.post("/cabinet/collaborators/{target_id}/revoke")
async def revoke_collaborator(
    target_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if target_id == user.id:
        raise HTTPException(400, "Vous ne pouvez pas révoquer votre propre accès.")
    access = await db.scalar(select(DossierAccess).where(
        DossierAccess.user_id == target_id, DossierAccess.tenant_id == user.tenant_id))
    if access:
        await db.delete(access)
        await db.commit()
    return {"revoked": True}


@router.post("/dossiers/create")
async def create_dossier(
    request: Request,
    company_name: str = Form(...),
    country: str = Form("MA"),
    currency: str = Form("MAD"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    slug_base = slugify(company_name)[:80] or "dossier"
    slug = slug_base
    n = 1
    while await db.scalar(select(Tenant).where(Tenant.slug == slug)):
        n += 1
        slug = f"{slug_base}-{n}"
    locale = "fr_MA" if country == "MA" else "fr_FR"
    tenant = Tenant(
        name=company_name, slug=slug, country=country, currency=currency, locale=locale,
        plan="pro", plan_status="trialing", trial_ends_at=utcnow() + timedelta(days=14),
    )
    db.add(tenant)
    await db.flush()
    db.add(DossierAccess(user_id=user.id, tenant_id=tenant.id, role="owner"))
    await db.commit()

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(ACTIVE_DOSSIER_COOKIE, str(tenant.id), max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite="lax")
    return resp
