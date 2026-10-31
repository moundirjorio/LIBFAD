"""Configuration du bot Telegram depuis l'interface (Automatisation → Bot Telegram)."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.telegram import TelegramLink
from app.models.tenant import Tenant
from app.models.user import User
from app.services import telegram_bot as bot

router = APIRouter()

LINK_CODE_MINUTES = 15

# Messages affichés sur la page après une action (?m=code) : (texte, type de toast)
MESSAGES = {
    "saved": ("Bot connecté. Reliez maintenant votre compte Telegram.", "success"),
    "disconnected": ("Bot déconnecté et comptes Telegram déliés.", "success"),
    "code": ("Code généré : ouvrez le lien ci-dessous depuis votre téléphone.", "success"),
    "unlinked": ("Compte Telegram délié.", "success"),
    "test_sent": ("Message de test envoyé sur Telegram.", "success"),
    "test_none": ("Aucun compte Telegram relié à votre utilisateur.", "error"),
    "test_failed": ("Telegram a refusé l'envoi du message de test.", "error"),
    "format": ("Ce jeton n'a pas le format attendu (ex. 123456789:AA…).", "error"),
    "refused": ("Telegram refuse ce jeton. Vérifiez-le dans @BotFather.", "error"),
    "unreachable": ("Telegram est injoignable pour le moment. Réessayez.", "error"),
    "used": ("Ce bot est déjà utilisé par une autre société.", "error"),
    "forbidden": ("Seul un propriétaire ou administrateur peut gérer le bot.", "error"),
    "no_bot": ("Connectez d'abord un bot.", "error"),
}


def can_manage(user: User) -> bool:
    return bool(user.is_superuser or user.role in ("owner", "admin"))


def _back(code: str) -> RedirectResponse:
    return RedirectResponse(url=f"/telegram?m={code}", status_code=303)


@router.post("/token")
async def save_token(
    token: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Enregistre le jeton @BotFather de la société et démarre le bot."""
    if not can_manage(user):
        return _back("forbidden")
    token = token.strip()
    if not bot.TOKEN_RE.match(token):
        return _back("format")
    taken = await db.scalar(
        select(Tenant.id).where(Tenant.telegram_bot_token == token, Tenant.id != user.tenant_id))
    if taken:
        return _back("used")
    try:
        me = await bot.tg_call(token, "getMe")
    except bot.TelegramError as exc:
        return _back("refused" if exc.code in (401, 404) else "unreachable")

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if tenant.telegram_bot_token and tenant.telegram_bot_token != token:
        # les conversations liées appartenaient à l'ancien bot
        await db.execute(delete(TelegramLink).where(TelegramLink.tenant_id == tenant.id))
    tenant.telegram_bot_token = token
    tenant.telegram_bot_username = me.get("username")
    await db.commit()
    try:
        await bot.register_commands(token)
    except bot.TelegramError:
        pass  # le menu de commandes est un confort, pas un prérequis
    await bot.start_poller(tenant.id, token)
    return _back("saved")


@router.post("/disconnect")
async def disconnect(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if not can_manage(user):
        return _back("forbidden")
    await bot.stop_poller(user.tenant_id)
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant.telegram_bot_token = None
    tenant.telegram_bot_username = None
    await db.execute(delete(TelegramLink).where(TelegramLink.tenant_id == tenant.id))
    await db.commit()
    return _back("disconnected")


@router.post("/link-code")
async def create_link_code(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Génère le code à usage unique qui relie une conversation Telegram à l'utilisateur connecté."""
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant.telegram_bot_token:
        return _back("no_bot")
    await db.execute(delete(TelegramLink).where(
        TelegramLink.tenant_id == tenant.id, TelegramLink.user_id == user.id, TelegramLink.chat_id.is_(None)))
    db.add(TelegramLink(
        tenant_id=tenant.id, user_id=user.id, code=secrets.token_urlsafe(9),
        code_expires_at=datetime.now(UTC) + timedelta(minutes=LINK_CODE_MINUTES),
    ))
    await db.commit()
    return _back("code")


@router.post("/links/{link_id}/delete")
async def delete_link(
    link_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    link = await db.scalar(select(TelegramLink).where(
        TelegramLink.id == link_id, TelegramLink.tenant_id == user.tenant_id))
    if link and (link.user_id == user.id or can_manage(user)):
        await db.delete(link)
        await db.commit()
    return _back("unlinked")


@router.post("/test")
async def send_test(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not tenant.telegram_bot_token:
        return _back("no_bot")
    links = list(await db.scalars(select(TelegramLink).where(
        TelegramLink.tenant_id == tenant.id, TelegramLink.user_id == user.id,
        TelegramLink.chat_id.is_not(None), TelegramLink.is_active.is_(True))))
    if not links:
        return _back("test_none")
    try:
        for link in links:
            await bot.send_text(tenant.telegram_bot_token, link.chat_id,
                                f"✅ Message de test envoyé depuis LIBFAD ({tenant.name}).", bot.MENU)
    except bot.TelegramError:
        return _back("test_failed")
    return _back("test_sent")
