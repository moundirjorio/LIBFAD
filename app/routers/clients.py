import csv
import io
import secrets
import string

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.deps import get_current_user, get_session
from app.models.client import Client, Contact
from app.models.client_portal import ClientPortalUser
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.client import ClientIn, ClientOut, ClientUpdate, ContactIn, ContactOut
from app.schemas.types import normalize_email
from app.security import hash_password
from app.services.email import send_email
from app.services.plans import enforce_quota
from app.services.solvency import compute_solvency, refresh_solvency


def _generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

router = APIRouter()


@router.get("", response_model=list[ClientOut])
async def list_clients(
    q: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Client).where(Client.tenant_id == user.tenant_id).order_by(Client.name)
    if q:
        stmt = stmt.where(Client.name.ilike(f"%{q}%"))
    rows = list(await db.scalars(stmt))
    return rows


@router.post("", response_model=ClientOut)
async def create_client(
    payload: ClientIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    await enforce_quota(db, tenant, "client")
    client = Client(tenant_id=user.tenant_id, **payload.model_dump())
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(
    client_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    client = await db.scalar(
        select(Client).where(Client.id == client_id, Client.tenant_id == user.tenant_id)
    )
    if not client:
        raise HTTPException(404, "Client introuvable")
    return client


@router.put("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: int,
    payload: ClientUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    client = await db.scalar(
        select(Client).where(Client.id == client_id, Client.tenant_id == user.tenant_id)
    )
    if not client:
        raise HTTPException(404, "Client introuvable")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(client, k, v)
    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}")
async def delete_client(
    client_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    client = await db.scalar(
        select(Client).where(Client.id == client_id, Client.tenant_id == user.tenant_id)
    )
    if not client:
        raise HTTPException(404, "Client introuvable")
    await db.delete(client)
    await db.commit()
    return {"deleted": True}


@router.post("/{client_id}/solvency", response_model=ClientOut)
async def recompute_solvency(
    client_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    client = await db.scalar(
        select(Client).where(Client.id == client_id, Client.tenant_id == user.tenant_id)
    )
    if not client:
        raise HTTPException(404, "Client introuvable")
    await refresh_solvency(db, client)
    await db.commit()
    await db.refresh(client)
    return client


# --- Import CSV -------------------------------------------------------------
# Colonnes reconnues (insensibles à la casse/espaces). Seul "name" est requis.
_CSV_FIELDS = {
    "name": "name", "nom": "name", "raison sociale": "name",
    "email": "email", "e-mail": "email", "mail": "email",
    "phone": "phone", "telephone": "phone", "téléphone": "phone", "tel": "phone",
    "city": "billing_city", "ville": "billing_city",
    "postal_code": "billing_postal_code", "code postal": "billing_postal_code", "cp": "billing_postal_code",
    "country": "billing_country", "pays": "billing_country",
    "address": "billing_address_line1", "adresse": "billing_address_line1",
    "ice": "ice", "vat_number": "vat_number", "tva": "vat_number", "siret": "siret",
}


@router.post("/import")
async def import_clients_csv(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Importe des clients depuis un CSV. Respecte le quota du plan (s'arrête et
    signale si la limite est atteinte). En-têtes FR ou EN, séparateur , ou ;."""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    # Détecte le séparateur (',' ou ';').
    sample = raw[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    if not reader.fieldnames:
        raise HTTPException(400, "Fichier CSV vide ou illisible.")

    # Mappe les colonnes du fichier vers nos champs.
    colmap = {}
    for col in reader.fieldnames:
        key = _CSV_FIELDS.get((col or "").strip().lower())
        if key:
            colmap[col] = key
    if "name" not in colmap.values():
        raise HTTPException(400, "Colonne 'name' (ou 'nom') introuvable dans le CSV.")

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    created, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):  # ligne 1 = en-tête
        data = {}
        for col, key in colmap.items():
            val = (row.get(col) or "").strip()
            if val:
                data[key] = val
        name = data.get("name")
        if not name:
            skipped += 1
            continue
        try:
            await enforce_quota(db, tenant, "client")
        except HTTPException as exc:
            errors.append(f"Ligne {i} : {exc.detail}")
            break  # quota atteint : inutile de continuer
        if "email" in data:
            data["email"] = normalize_email(data["email"])
        db.add(Client(tenant_id=user.tenant_id, **data))
        await db.flush()
        created += 1

    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


# --- Accès au portail client ------------------------------------------------
@router.post("/{client_id}/portal-access")
async def create_or_reset_portal_access(
    client_id: int,
    email: str | None = Form(None),
    notify: str | None = Form("1"),  # envoyer les identifiants par email au client
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Crée l'accès au portail client (ou réinitialise le mot de passe).

    Renvoie l'email et un mot de passe généré, à transmettre au client.
    Le mot de passe n'est jamais stocké en clair — il n'est visible qu'ici.
    Si `notify` est actif, les identifiants sont aussi envoyés par email au client.
    """
    client = await db.scalar(
        select(Client).where(Client.id == client_id, Client.tenant_id == user.tenant_id)
    )
    if not client:
        raise HTTPException(404, "Client introuvable")

    login_email = normalize_email(email) if email else (client.email or "").strip().lower()
    if not login_email:
        raise HTTPException(400, "Aucun email : renseignez l'email du client d'abord.")

    portal_user = await db.scalar(
        select(ClientPortalUser).where(ClientPortalUser.client_id == client_id)
    )
    # Empêche de réutiliser un email déjà pris par un autre accès portail.
    clash = await db.scalar(
        select(ClientPortalUser).where(
            ClientPortalUser.email == login_email,
            ClientPortalUser.client_id != client_id,
        )
    )
    if clash:
        raise HTTPException(400, "Cet email est déjà utilisé par un autre accès client.")

    password = _generate_password()
    if portal_user:
        portal_user.email = login_email
        portal_user.password_hash = hash_password(password)
        portal_user.is_active = True
        action = "reset"
    else:
        portal_user = ClientPortalUser(
            tenant_id=user.tenant_id,
            client_id=client_id,
            email=login_email,
            password_hash=hash_password(password),
            full_name=client.name,
        )
        db.add(portal_user)
        action = "created"
    await db.commit()

    login_url = f"{settings.base_url.rstrip('/')}/portail/login"
    emailed = False
    if notify:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
        company = (tenant.name if tenant else None) or "votre fournisseur"
        subject = f"Votre accès à l'espace client — {company}"
        body = (
            f"Bonjour,\n\n{company} vous donne accès à votre espace client en ligne, "
            f"où vous pouvez consulter vos factures et devis, suivre vos paiements et "
            f"télécharger vos documents.\n\n"
            f"Adresse de connexion : {login_url}\n"
            f"Identifiant (email) : {login_email}\n"
            f"Mot de passe : {password}\n\n"
            f"Pour votre sécurité, pensez à ne pas partager ces identifiants.\n\n"
            f"Cordialement,\n{company}"
        )
        try:
            await send_email(to=login_email, subject=subject, body_text=body)
            emailed = True
        except Exception:
            emailed = False

    return {
        "action": action,
        "email": login_email,
        "password": password,
        "login_url": login_url,
        "emailed": emailed,
    }


@router.post("/portal-access/bulk")
async def create_portal_access_bulk(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Crée l'accès au portail pour TOUS les clients ayant un email et n'en ayant
    pas déjà un. Envoie les identifiants par email à chacun."""
    clients = list(await db.scalars(
        select(Client).where(Client.tenant_id == user.tenant_id).order_by(Client.name)
    ))
    # Emails déjà utilisés par un accès portail (tous tenants) pour éviter les collisions.
    existing_client_ids = set(await db.scalars(
        select(ClientPortalUser.client_id).where(ClientPortalUser.tenant_id == user.tenant_id)
    ))
    used_emails = set(await db.scalars(select(ClientPortalUser.email)))

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    company = (tenant.name if tenant else None) or "votre fournisseur"
    login_url = f"{settings.base_url.rstrip('/')}/portail/login"

    created = 0
    skipped_no_email = 0
    skipped_existing = 0
    skipped_email_taken = 0
    to_email: list[tuple[str, str]] = []

    for client in clients:
        if client.id in existing_client_ids:
            skipped_existing += 1
            continue
        email = (client.email or "").strip().lower()
        if not email:
            skipped_no_email += 1
            continue
        try:
            email = normalize_email(email)
        except ValueError:
            skipped_no_email += 1
            continue
        if email in used_emails:
            skipped_email_taken += 1
            continue

        password = _generate_password()
        db.add(ClientPortalUser(
            tenant_id=user.tenant_id, client_id=client.id, email=email,
            password_hash=hash_password(password), full_name=client.name,
        ))
        used_emails.add(email)
        to_email.append((email, password))
        created += 1

    await db.commit()

    emailed = 0
    for email, password in to_email:
        body = (
            f"Bonjour,\n\n{company} vous donne accès à votre espace client en ligne.\n\n"
            f"Adresse de connexion : {login_url}\n"
            f"Identifiant (email) : {email}\n"
            f"Mot de passe : {password}\n\n"
            f"Cordialement,\n{company}"
        )
        try:
            await send_email(to=email, subject=f"Votre accès à l'espace client — {company}", body_text=body)
            emailed += 1
        except Exception:
            pass

    return {
        "created": created,
        "emailed": emailed,
        "skipped_existing": skipped_existing,
        "skipped_no_email": skipped_no_email,
        "skipped_email_taken": skipped_email_taken,
    }


@router.post("/{client_id}/portal-access/toggle")
async def toggle_portal_access(
    client_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Active/désactive l'accès au portail sans le supprimer."""
    portal_user = await db.scalar(
        select(ClientPortalUser)
        .join(Client, ClientPortalUser.client_id == Client.id)
        .where(ClientPortalUser.client_id == client_id, Client.tenant_id == user.tenant_id)
    )
    if not portal_user:
        raise HTTPException(404, "Aucun accès portail pour ce client.")
    portal_user.is_active = not portal_user.is_active
    await db.commit()
    return {"is_active": portal_user.is_active}


# --- Contacts ---------------------------------------------------------------
@router.get("/{client_id}/contacts", response_model=list[ContactOut])
async def list_contacts(
    client_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return list(
        await db.scalars(
            select(Contact)
            .where(Contact.client_id == client_id, Contact.tenant_id == user.tenant_id)
            .order_by(Contact.is_primary.desc(), Contact.id)
        )
    )


@router.post("/{client_id}/contacts", response_model=ContactOut)
async def create_contact(
    client_id: int,
    payload: ContactIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    contact = Contact(tenant_id=user.tenant_id, client_id=client_id, **payload.model_dump())
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


# --- HTML form endpoints ----------------------------------------------------
@router.post("/form")
async def create_client_form(
    name: str = Form(...),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    siret: str | None = Form(None),
    vat_number: str | None = Form(None),
    ice: str | None = Form(None),
    billing_address_line1: str | None = Form(None),
    billing_city: str | None = Form(None),
    billing_postal_code: str | None = Form(None),
    billing_country: str = Form("FR"),
    currency: str = Form("EUR"),
    payment_terms_days: int = Form(30),
    create_portal: str | None = Form(None),  # case cochée = créer l'accès portail
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    await enforce_quota(db, tenant, "client")
    client = Client(
        tenant_id=user.tenant_id,
        name=name,
        email=email,
        phone=phone,
        siret=siret,
        vat_number=vat_number,
        ice=ice,
        billing_address_line1=billing_address_line1,
        billing_city=billing_city,
        billing_postal_code=billing_postal_code,
        billing_country=billing_country,
        currency=currency,
        payment_terms_days=payment_terms_days,
    )
    db.add(client)
    await db.flush()

    # Accès portail optionnel (si demandé et si le client a un email non déjà pris).
    if create_portal and (email or "").strip():
        login_email = normalize_email(email)
        taken = await db.scalar(
            select(ClientPortalUser).where(ClientPortalUser.email == login_email)
        )
        if not taken:
            password = _generate_password()
            db.add(ClientPortalUser(
                tenant_id=user.tenant_id, client_id=client.id, email=login_email,
                password_hash=hash_password(password), full_name=client.name,
            ))
            await db.flush()
            company = (tenant.name if tenant else None) or "votre fournisseur"
            login_url = f"{settings.base_url.rstrip('/')}/portail/login"
            body = (
                f"Bonjour,\n\n{company} vous donne accès à votre espace client en ligne.\n\n"
                f"Adresse de connexion : {login_url}\n"
                f"Identifiant (email) : {login_email}\n"
                f"Mot de passe : {password}\n\n"
                f"Cordialement,\n{company}"
            )
            try:
                await send_email(to=login_email, subject=f"Votre accès à l'espace client — {company}", body_text=body)
            except Exception:
                pass

    await db.commit()
    return RedirectResponse(url=f"/clients/{client.id}", status_code=303)
