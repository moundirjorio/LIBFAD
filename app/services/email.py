"""Envoi d'emails via SMTP."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import aiosmtplib

from app.config import settings


async def send_email(
    *,
    to: str | list[str],
    subject: str,
    body_text: str | None = None,
    body_html: str | None = None,
    attachments: list[Path] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
) -> bool:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    msg["To"] = ", ".join(to) if isinstance(to, list) else to
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject

    if body_text:
        msg.set_content(body_text)
    if body_html:
        if body_text:
            msg.add_alternative(body_html, subtype="html")
        else:
            msg.set_content(body_html, subtype="html")

    if attachments:
        for path in attachments:
            data = Path(path).read_bytes()
            ctype = "application/pdf" if str(path).lower().endswith(".pdf") else "application/octet-stream"
            maintype, _, subtype = ctype.partition("/")
            msg.add_attachment(
                data, maintype=maintype, subtype=subtype, filename=Path(path).name
            )

    recipients: list[str] = []
    recipients.extend([to] if isinstance(to, str) else to)
    if cc:
        recipients.extend(cc)
    if bcc:
        recipients.extend(bcc)

    if not settings.smtp_configured:
        # SMTP non configuré (valeurs d'exemple) : en dev on log et on considère l'envoi OK,
        # en production on échoue clairement plutôt que de tenter un envoi voué à l'échec.
        import logging

        if settings.app_env == "development":
            logging.warning(
                "[EMAIL DEV MODE] SMTP non configuré — email simulé.\nÀ: %s\nSujet: %s\n---\n%s",
                recipients, subject, body_text or body_html,
            )
            return True
        raise RuntimeError(
            "SMTP non configuré : renseigne SMTP_USER / SMTP_PASSWORD dans .env pour envoyer des emails."
        )

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_tls,
        recipients=recipients,
    )
    return True
