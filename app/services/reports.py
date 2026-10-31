"""Rapports financiers (CA, top clients, balance âgée)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment


async def revenue_summary(db: AsyncSession, tenant_id: int, year: int | None = None) -> dict:
    year = year or date.today().year
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    rows = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id)
            .where(Invoice.issue_date >= start)
            .where(Invoice.issue_date <= end)
            .where(Invoice.is_credit_note == False)  # noqa: E712
        )
    )

    revenue = sum(Decimal(str(i.total_ttc)) for i in rows if i.status != "cancelled")
    collected = sum(Decimal(str(i.amount_paid)) for i in rows)
    outstanding = sum(Decimal(str(i.amount_due)) for i in rows if i.amount_due > 0)

    monthly = [Decimal(0) for _ in range(12)]
    for inv in rows:
        if inv.status != "cancelled":
            monthly[inv.issue_date.month - 1] += Decimal(str(inv.total_ttc))

    return {
        "year": year,
        "total_revenue": float(revenue),
        "total_collected": float(collected),
        "outstanding": float(outstanding),
        "monthly": [float(m) for m in monthly],
        "invoice_count": len(rows),
    }


async def aged_balance(db: AsyncSession, tenant_id: int) -> dict:
    """Balance âgée : 0-30, 31-60, 61-90, 90+ jours de retard."""
    today = date.today()
    buckets = {"0_30": Decimal(0), "31_60": Decimal(0), "61_90": Decimal(0), "over_90": Decimal(0)}
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.amount_due > 0)
        .where(Invoice.status != "cancelled")
    )
    for inv in rows:
        delay = (today - inv.due_date).days
        amt = Decimal(str(inv.amount_due))
        if delay <= 30:
            buckets["0_30"] += amt
        elif delay <= 60:
            buckets["31_60"] += amt
        elif delay <= 90:
            buckets["61_90"] += amt
        else:
            buckets["over_90"] += amt
    return {k: float(v) for k, v in buckets.items()}


async def top_clients(db: AsyncSession, tenant_id: int, limit: int = 10) -> list[dict]:
    stmt = (
        select(
            Client.id,
            Client.name,
            func.coalesce(func.sum(Invoice.total_ttc), 0).label("revenue"),
            func.count(Invoice.id).label("invoice_count"),
        )
        .join(Invoice, Invoice.client_id == Client.id)
        .where(Client.tenant_id == tenant_id)
        .where(Invoice.status != "cancelled")
        .group_by(Client.id, Client.name)
        .order_by(func.sum(Invoice.total_ttc).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "client_id": r.id,
            "name": r.name,
            "revenue": float(r.revenue or 0),
            "invoice_count": r.invoice_count,
        }
        for r in rows
    ]


async def dashboard_kpis(db: AsyncSession, tenant_id: int) -> dict:
    today = date.today()
    year = today.year

    revenue = await revenue_summary(db, tenant_id, year)
    aged = await aged_balance(db, tenant_id)

    overdue_count = await db.scalar(
        select(func.count(Invoice.id))
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.status == "overdue")
    )
    draft_count = await db.scalar(
        select(func.count(Invoice.id))
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.status == "draft")
    )
    client_count = await db.scalar(
        select(func.count(Client.id)).where(Client.tenant_id == tenant_id)
    )

    return {
        "revenue_year": revenue["total_revenue"],
        "collected_year": revenue["total_collected"],
        "outstanding": revenue["outstanding"],
        "monthly_revenue": revenue["monthly"],
        "aged_balance": aged,
        "overdue_count": overdue_count or 0,
        "draft_count": draft_count or 0,
        "client_count": client_count or 0,
    }
