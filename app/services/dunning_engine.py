"""Moteur de relances automatiques."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.client import Client, Contact
from app.models.dunning import DunningMessage, DunningRun, DunningScenario, DunningStep
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.services.ai import generate_dunning_email
from app.services.email import send_email
from app.services.pdf import storage_pdf_path


async def mark_overdue_invoices(db: AsyncSession, tenant_id: int) -> int:
    """Passe en statut 'overdue' les factures dont la date d'échéance est passée."""
    today = date.today()
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.status.in_(["issued", "sent", "partially_paid"]))
        .where(Invoice.due_date < today)
        .where(Invoice.amount_due > 0)
    )
    count = 0
    for inv in rows:
        inv.status = "overdue"
        count += 1
    await db.flush()
    return count


async def start_dunning_for_invoice(
    db: AsyncSession, invoice: Invoice, scenario_id: int | None = None
) -> DunningRun:
    """Démarre (ou récupère) un DunningRun pour une facture."""
    existing = await db.scalar(
        select(DunningRun)
        .where(DunningRun.invoice_id == invoice.id)
        .where(DunningRun.status.in_(["running", "paused"]))
    )
    if existing:
        return existing

    if scenario_id is None:
        scenario = await db.scalar(
            select(DunningScenario)
            .where(DunningScenario.tenant_id == invoice.tenant_id)
            .where(DunningScenario.is_default == True)  # noqa: E712
            .where(DunningScenario.is_active == True)  # noqa: E712
        )
        if not scenario:
            raise RuntimeError("Aucun scénario de relance par défaut configuré.")
        scenario_id = scenario.id

    run = DunningRun(
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        scenario_id=scenario_id,
        status="running",
        current_step_index=0,
        next_action_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    return run


async def execute_due_dunning_runs(db: AsyncSession, tenant_id: int) -> dict:
    """Pour chaque DunningRun actif dont next_action_at est passé, exécute l'étape suivante."""
    now = datetime.now(UTC)
    runs = list(
        await db.scalars(
            select(DunningRun)
            .where(DunningRun.tenant_id == tenant_id)
            .where(DunningRun.status == "running")
            .where(DunningRun.next_action_at <= now)
            .options(selectinload(DunningRun.messages))
        )
    )
    sent = 0
    closed = 0
    for run in runs:
        result = await _execute_one_step(db, run)
        if result == "sent":
            sent += 1
        elif result == "completed":
            closed += 1
    return {"sent": sent, "completed": closed, "checked": len(runs)}


async def _execute_one_step(db: AsyncSession, run: DunningRun) -> str:
    invoice = await db.scalar(select(Invoice).where(Invoice.id == run.invoice_id))
    if invoice is None or invoice.amount_due <= 0 or invoice.status == "paid":
        run.status = "resolved"
        run.resolved_at = date.today()
        return "completed"

    scenario = await db.scalar(
        select(DunningScenario)
        .where(DunningScenario.id == run.scenario_id)
        .options(selectinload(DunningScenario.steps))
    )
    if not scenario or not scenario.steps:
        run.status = "stopped"
        return "completed"

    steps = sorted(scenario.steps, key=lambda s: s.order_index)
    if run.current_step_index >= len(steps):
        run.status = "completed"
        return "completed"

    step = steps[run.current_step_index]
    client = await db.scalar(select(Client).where(Client.id == invoice.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == invoice.tenant_id))
    contact = await db.scalar(
        select(Contact)
        .where(Contact.client_id == invoice.client_id)
        .where(Contact.receives_dunning == True)  # noqa: E712
        .order_by(Contact.is_primary.desc())
    )
    to_email = (contact.email if contact else None) or (client.email if client else None)

    locale = step.locale or (client.locale if client else "fr_FR")
    days_overdue = max(0, (date.today() - invoice.due_date).days)

    # Génération du contenu (template > IA)
    if step.subject_template and step.body_template:
        subject = step.subject_template.format(
            invoice_number=invoice.number,
            client_name=client.name if client else "",
            amount=float(invoice.amount_due),
            days=days_overdue,
        )
        body = step.body_template.format(
            invoice_number=invoice.number,
            client_name=client.name if client else "",
            amount=float(invoice.amount_due),
            days=days_overdue,
            company_name=tenant.name if tenant else "",
        )
        ai_used = False
    else:
        try:
            gen = await generate_dunning_email(
                tone=step.tone or "polite",
                locale=locale,
                client_name=client.name if client else "Client",
                invoice_number=invoice.number,
                invoice_amount=f"{float(invoice.amount_due):.2f} {invoice.currency}",
                days_overdue=days_overdue,
                due_date=invoice.due_date.isoformat(),
                company_name=tenant.name if tenant else "",
                company_signature=tenant.name if tenant else None,
            )
            subject = gen.get("subject", f"Relance facture {invoice.number}")
            body = gen.get("body", "")
            ai_used = True
        except Exception as exc:
            # L'IA est indisponible (clé manquante, quota, réseau) : on n'interrompt pas
            # tout le batch — on marque cette relance en échec et on passe à la suivante.
            message = DunningMessage(
                tenant_id=invoice.tenant_id,
                run_id=run.id,
                step_id=step.id,
                channel="email",
                direction="outbound",
                to_email=to_email,
                subject=f"Relance facture {invoice.number}",
                body="",
                locale=locale,
                tone=step.tone,
                ai_generated=True,
                status="failed",
                error=f"Génération IA échouée : {exc}",
            )
            db.add(message)
            await db.flush()
            return "failed"

    message = DunningMessage(
        tenant_id=invoice.tenant_id,
        run_id=run.id,
        step_id=step.id,
        channel="email",
        direction="outbound",
        to_email=to_email,
        subject=subject,
        body=body,
        locale=locale,
        tone=step.tone,
        ai_generated=ai_used,
        status="queued",
    )
    db.add(message)
    await db.flush()

    # Envoi
    try:
        attachments = []
        if invoice.pdf_path:
            from pathlib import Path

            p = Path(invoice.pdf_path)
            if p.exists():
                attachments.append(p)
        if to_email:
            await send_email(
                to=to_email, subject=subject, body_text=body, attachments=attachments or None
            )
            message.status = "sent"
            message.sent_at = datetime.now(UTC)
            invoice.last_reminder_at = date.today()
            invoice.reminder_count = (invoice.reminder_count or 0) + 1
            outcome = "sent"
        else:
            message.status = "failed"
            message.error = "Pas d'email destinataire"
            outcome = "failed"
    except Exception as exc:  # pragma: no cover
        message.status = "failed"
        message.error = str(exc)
        outcome = "failed"

    run.current_step_index += 1
    if run.current_step_index >= len(steps):
        run.status = "completed"
    else:
        next_step = steps[run.current_step_index]
        # next_action_at = due_date + offset_days (mais pas avant maintenant)
        target = datetime.combine(invoice.due_date + timedelta(days=next_step.offset_days), datetime.min.time(), tzinfo=UTC)
        run.next_action_at = max(target, datetime.now(UTC) + timedelta(hours=1))

    await db.flush()
    return outcome
