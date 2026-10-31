from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_user, get_session
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter()

_LOGO_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}


@router.get("/me")
async def get_my_tenant(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant introuvable")
    return {
        "id": tenant.id,
        "name": tenant.name,
        "legal_name": tenant.legal_name,
        "email": tenant.email,
        "phone": tenant.phone,
        "siret": tenant.siret,
        "vat_number": tenant.vat_number,
        "ice": tenant.ice,
        "address_line1": tenant.address_line1,
        "city": tenant.city,
        "postal_code": tenant.postal_code,
        "country": tenant.country,
        "currency": tenant.currency,
        "locale": tenant.locale,
        "iban": tenant.iban,
        "bic": tenant.bic,
        "bank_name": tenant.bank_name,
    }


@router.post("/me")
async def update_my_tenant(
    name: str | None = Form(None),
    legal_name: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    siret: str | None = Form(None),
    vat_number: str | None = Form(None),
    ice: str | None = Form(None),
    rc: str | None = Form(None),
    website: str | None = Form(None),
    address_line1: str | None = Form(None),
    city: str | None = Form(None),
    postal_code: str | None = Form(None),
    country: str | None = Form(None),
    currency: str | None = Form(None),
    locale: str | None = Form(None),
    primary_color: str | None = Form(None),
    pdf_template: str | None = Form(None),
    iban: str | None = Form(None),
    bic: str | None = Form(None),
    bank_name: str | None = Form(None),
    default_bank_account_id: int | None = Form(None),
    invoice_footer: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant introuvable")
    # Compte bancaire par défaut : accepte aussi la remise à zéro (vide).
    if default_bank_account_id is not None:
        from app.models.bank import BankAccount

        acc = await db.scalar(select(BankAccount).where(
            BankAccount.id == default_bank_account_id, BankAccount.tenant_id == user.tenant_id))
        tenant.default_bank_account_id = acc.id if acc else None

    for field, value in {
        "name": name,
        "legal_name": legal_name,
        "email": email,
        "phone": phone,
        "siret": siret,
        "vat_number": vat_number,
        "ice": ice,
        "rc": rc,
        "website": website,
        "address_line1": address_line1,
        "city": city,
        "postal_code": postal_code,
        "country": country,
        "currency": currency,
        "locale": locale,
        "primary_color": primary_color,
        "pdf_template": pdf_template,
        "iban": iban,
        "bic": bic,
        "bank_name": bank_name,
        "invoice_footer": invoice_footer,
    }.items():
        if value is not None and value != "":
            setattr(tenant, field, value)
    await db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/me/numbering")
async def update_numbering(
    invoice_number_format: str = Form("FA-{YYYY}-{seq}"),
    quote_number_format: str = Form("DV-{YYYY}-{seq}"),
    number_padding: int = Form(5),
    number_reset_yearly: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant introuvable")
    tenant.invoice_number_format = invoice_number_format.strip() or "FA-{YYYY}-{seq}"
    tenant.quote_number_format = quote_number_format.strip() or "DV-{YYYY}-{seq}"
    tenant.number_padding = max(1, min(int(number_padding), 12))
    # Case à cocher : présente = True, absente = False.
    tenant.number_reset_yearly = number_reset_yearly is not None
    await db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/me/logo")
async def get_logo(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant or not tenant.logo_path or not Path(tenant.logo_path).exists():
        raise HTTPException(404, "Aucun logo")
    return FileResponse(tenant.logo_path)


@router.post("/me/logo")
async def upload_logo(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant introuvable")
    if file.content_type not in _LOGO_EXT:
        raise HTTPException(400, "Format de logo non supporté (PNG, JPEG ou GIF).")

    raw = await file.read()
    # Valide que l'image est réellement décodable avant de l'enregistrer.
    import io

    from PIL import Image

    try:
        Image.open(io.BytesIO(raw)).verify()
    except Exception:
        raise HTTPException(400, "Image illisible ou corrompue.") from None

    logo_dir = settings.storage_path / "branding" / f"tenant_{tenant.id}"
    logo_dir.mkdir(parents=True, exist_ok=True)
    # Nettoie d'éventuels anciens logos pour ne garder qu'un fichier.
    for old in logo_dir.glob("logo.*"):
        old.unlink(missing_ok=True)
    target = logo_dir / f"logo{_LOGO_EXT[file.content_type]}"
    target.write_bytes(raw)

    tenant.logo_path = str(target)
    await db.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/me/logo/delete")
async def delete_logo(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant introuvable")
    if tenant.logo_path:
        Path(tenant.logo_path).unlink(missing_ok=True)
        tenant.logo_path = None
        await db.commit()
    return RedirectResponse(url="/settings", status_code=303)
