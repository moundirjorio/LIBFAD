"""Tâches planifiées : récurrentes (abonnements) + relances.

Exposé en endpoints à appeler par un cron externe (Windows Task Scheduler / cron).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceItem
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.services.billing import compute_document_totals, compute_line
from app.services.dunning_engine import (
    execute_due_dunning_runs,
    mark_overdue_invoices,
    start_dunning_for_invoice,
)
from app.services.numbering import next_number


def _add_period(d: date, period: str, interval: int) -> date:
    interval = max(1, interval or 1)
    if period == "daily":
        return d + timedelta(days=interval)
    if period == "weekly":
        return d + timedelta(weeks=interval)
    if period == "monthly":
        month = d.month + interval
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)  # safe pour février
        return date(year, month, day)
    if period == "quarterly":
        return _add_period(d, "monthly", 3 * interval)
    if period == "yearly":
        return date(d.year + interval, d.month, d.day)
    return d + timedelta(days=30 * interval)


async def generate_subscription_invoices(db: AsyncSession, tenant_id: int) -> int:
    """Crée les factures dues pour les abonnements actifs."""
    today = date.today()
    subs = list(
        await db.scalars(
            select(Subscription)
            .where(Subscription.tenant_id == tenant_id)
            .where(Subscription.is_active == True)  # noqa: E712
            .where(Subscription.next_billing_date <= today)
        )
    )
    created = 0
    for sub in subs:
        if sub.end_date and sub.end_date < today:
            sub.is_active = False
            continue

        number = await next_number(db, tenant_id, "invoice")
        line = compute_line(
            quantity=1, unit_price=sub.amount_ht, discount_rate=0, tax_rate=sub.tax_rate
        )
        totals = compute_document_totals([line])

        invoice = Invoice(
            tenant_id=tenant_id,
            number=number,
            client_id=sub.client_id,
            subscription_id=sub.id,
            status="issued",
            issue_date=today,
            due_date=today + timedelta(days=30),
            currency=sub.currency,
            subtotal_ht=totals["subtotal_ht"],
            total_tax=totals["total_tax"],
            total_ttc=totals["total_ttc"],
            amount_due=totals["total_ttc"],
            amount_paid=Decimal(0),
        )
        db.add(invoice)
        await db.flush()

        item = InvoiceItem(
            tenant_id=tenant_id,
            invoice_id=invoice.id,
            description=sub.name,
            quantity=Decimal(1),
            unit_price=sub.amount_ht,
            tax_rate=sub.tax_rate,
            position=0,
            **line,
        )
        db.add(item)

        sub.last_billing_date = today
        sub.next_billing_date = _add_period(sub.next_billing_date, sub.period, sub.interval)
        created += 1
    await db.flush()
    return created


async def run_daily_jobs(db: AsyncSession, tenant_id: int) -> dict:
    """À appeler une fois par jour."""
    created = await generate_subscription_invoices(db, tenant_id)
    overdue = await mark_overdue_invoices(db, tenant_id)

    # Démarre une relance pour chaque facture en retard sans run actif
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.status == "overdue")
        .where(Invoice.amount_due > 0)
    )
    started = 0
    for inv in rows:
        try:
            await start_dunning_for_invoice(db, inv)
            started += 1
        except RuntimeError:
            break  # pas de scénario configuré

    dunning_stats = await execute_due_dunning_runs(db, tenant_id)
    await db.commit()
    return {
        "subscriptions_invoiced": created,
        "marked_overdue": overdue,
        "dunning_runs_started": started,
        "dunning_messages_sent": dunning_stats.get("sent", 0),
    }


async def run_daily_jobs_all_tenants(db: AsyncSession) -> dict:
    """Exécute le job quotidien pour chaque tenant actif. Appelé par le planificateur."""
    tenant_ids = list(
        await db.scalars(select(Tenant.id).where(Tenant.is_active == True))  # noqa: E712
    )
    totals = {
        "tenants": 0,
        "subscriptions_invoiced": 0,
        "marked_overdue": 0,
        "dunning_runs_started": 0,
        "dunning_messages_sent": 0,
    }
    for tid in tenant_ids:
        stats = await run_daily_jobs(db, tid)
        totals["tenants"] += 1
        for key in ("subscriptions_invoiced", "marked_overdue", "dunning_runs_started", "dunning_messages_sent"):
            totals[key] += stats.get(key, 0)
    return totals
