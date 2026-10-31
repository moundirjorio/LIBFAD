from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_session
from app.models.tenant import Tenant
from app.models.user import User
from app.ratelimit import is_blocked, record_failure, reset as reset_ratelimit
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.schemas.types import normalize_email, validate_email_lenient
from app.security import create_access_token, hash_password, verify_password
from app.services.email import send_email
from app.services.password_reset import consume, create_token, verify_token
from slugify import slugify

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request, "settings": settings})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request, "settings": settings})


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("libfad_token")
    return resp


# --- Mot de passe oublié (comptes entreprise) -------------------------------
@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("auth/forgot.html", {"request": request, "settings": settings})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    # Réponse générique quoi qu'il arrive : n'expose pas l'existence d'un compte.
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if user and user.is_active:
        token = await create_token(db, "company", user.id)
        await db.commit()
        link = f"{settings.base_url.rstrip('/')}/reset-password?token={token}"
        await send_email(
            to=user.email,
            subject=f"Réinitialisation de votre mot de passe — {settings.app_name}",
            body_text=(
                f"Bonjour,\n\nVous avez demandé à réinitialiser votre mot de passe.\n"
                f"Cliquez sur ce lien (valable 1 heure) :\n{link}\n\n"
                f"Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.\n"
            ),
        )
    return templates.TemplateResponse(
        "auth/forgot.html",
        {"request": request, "settings": settings, "sent": True},
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = "", db: AsyncSession = Depends(get_session)):
    valid = bool(await verify_token(db, "company", token)) if token else False
    return templates.TemplateResponse(
        "auth/reset.html", {"request": request, "settings": settings, "token": token, "valid": valid}
    )


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password_form(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    row = await verify_token(db, "company", token)
    if not row:
        return templates.TemplateResponse(
            "auth/reset.html",
            {"request": request, "settings": settings, "token": token, "valid": False,
             "error": "Lien invalide ou expiré. Refaites une demande."},
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/reset.html",
            {"request": request, "settings": settings, "token": token, "valid": True,
             "error": "Le mot de passe doit faire au moins 8 caractères."},
            status_code=400,
        )
    user = await db.scalar(select(User).where(User.id == row.subject_id))
    if user:
        user.password_hash = hash_password(password)
    consume(row)
    await db.commit()
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "settings": settings, "info": "Mot de passe mis à jour. Vous pouvez vous connecter."},
    )


def _make_unique_slug(base: str) -> str:
    return slugify(base)[:80] or "tenant"


@router.post("/api/auth/register", response_model=UserOut)
async def api_register(payload: RegisterIn, db: AsyncSession = Depends(get_session)):
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status_code=400, detail="Email déjà utilisé")

    slug_base = _make_unique_slug(payload.company_name)
    slug = slug_base
    counter = 1
    while await db.scalar(select(Tenant).where(Tenant.slug == slug)):
        counter += 1
        slug = f"{slug_base}-{counter}"

    from datetime import timedelta

    from app.models.base import utcnow

    tenant = Tenant(
        name=payload.company_name,
        slug=slug,
        country=payload.country,
        currency=payload.currency,
        locale=payload.locale,
        plan="pro",
        plan_status="trialing",
        trial_ends_at=utcnow() + timedelta(days=14),
    )
    db.add(tenant)
    await db.flush()

    user = User(
        tenant_id=tenant.id,
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role="owner",
        locale=payload.locale,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/api/auth/login", response_model=TokenOut)
async def api_login(payload: LoginIn, db: AsyncSession = Depends(get_session)):
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Compte désactivé")
    token = create_access_token(subject=str(user.id), extra={"tid": user.tenant_id, "role": user.role})
    return TokenOut(access_token=token)


# --- Endpoints HTML form ----------------------------------------------------
@router.post("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    rl_key = f"login:{request.client.host if request.client else '?'}:{normalize_email(email)}"
    if is_blocked(rl_key):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "settings": settings,
             "error": "Trop de tentatives. Réessayez dans quelques minutes."},
            status_code=429,
        )

    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if not user or not verify_password(password, user.password_hash):
        record_failure(rl_key)
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "settings": settings, "error": "Email ou mot de passe incorrect"},
            status_code=401,
        )
    reset_ratelimit(rl_key)
    token = create_access_token(subject=str(user.id), extra={"tid": user.tenant_id, "role": user.role})
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        key="libfad_token",
        value=token,
        max_age=settings.jwt_expires_minutes * 60,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
    )
    return resp


@router.post("/register", response_class=HTMLResponse)
async def register_form(
    request: Request,
    company_name: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    country: str = Form("FR"),
    currency: str = Form("EUR"),
    db: AsyncSession = Depends(get_session),
):
    try:
        email = validate_email_lenient(email)
    except ValueError:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "settings": settings, "error": "Adresse email invalide"},
            status_code=400,
        )
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "settings": settings, "error": "Email déjà utilisé"},
            status_code=400,
        )
    slug_base = _make_unique_slug(company_name)
    slug = slug_base
    counter = 1
    while await db.scalar(select(Tenant).where(Tenant.slug == slug)):
        counter += 1
        slug = f"{slug_base}-{counter}"

    locale = "fr_MA" if country == "MA" else "fr_FR"
    from datetime import timedelta

    from app.models.base import utcnow

    tenant = Tenant(
        name=company_name, slug=slug, country=country, currency=currency, locale=locale,
        plan="pro", plan_status="trialing", trial_ends_at=utcnow() + timedelta(days=14),
    )
    db.add(tenant)
    await db.flush()

    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role="owner",
        locale=locale,
    )
    db.add(user)
    await db.flush()
    from app.models.dossier import DossierAccess

    db.add(DossierAccess(user_id=user.id, tenant_id=tenant.id, role="owner"))
    await db.commit()

    token = create_access_token(subject=str(user.id), extra={"tid": tenant.id, "role": "owner"})
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        "libfad_token", token, max_age=settings.jwt_expires_minutes * 60,
        httponly=True, samesite=settings.cookie_samesite, secure=settings.cookie_secure,
    )
    return resp
