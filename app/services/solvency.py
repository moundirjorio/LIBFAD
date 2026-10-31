"""Calcul du score de solvabilité client (0-100) basé sur l'historique de paiement."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.invoice import Invoice


async def compute_solvency(db: AsyncSession, client: Client) -> dict:
    """Renvoie {score: int 0-100, level: low|medium|high, factors: dict}.

    Heuristique simple :
    - Démarre à 100
    - -30 si retard moyen > 30 jours
    - -15 si retard moyen > 10 jours
    - -10 par facture impayée
    - -5 par facture overdue active
    - +5 si tout payé à temps sur > 5 factures
    """
    rows = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == client.tenant_id)
            .where(Invoice.client_id == client.id)
        )
    )
    today = date.today()

    if not rows:
        return {"score": None, "level": "unknown", "factors": {"reason": "Aucune facture"}}

    paid = [i for i in rows if i.status == "paid"]
    overdue = [i for i in rows if i.status == "overdue"]
    unpaid = [i for i in rows if i.amount_due > 0]

    delays = []
    for inv in paid:
        if inv.paid_at and inv.due_date:
            delays.append((inv.paid_at - inv.due_date).days)
    avg_delay = sum(delays) / len(delays) if delays else 0

    score = 100
    if avg_delay > 30:
        score -= 30
    elif avg_delay > 10:
        score -= 15
    elif avg_delay > 0:
        score -= 5

    score -= 10 * len(unpaid)
    score -= 5 * len(overdue)

    if len(paid) > 5 and avg_delay <= 0:
        score += 5

    score = max(0, min(100, score))

    if score >= 75:
        level = "low"
    elif score >= 45:
        level = "medium"
    else:
        level = "high"

    return {
        "score": score,
        "level": level,
        "factors": {
            "invoices_total": len(rows),
            "invoices_paid": len(paid),
            "invoices_unpaid": len(unpaid),
            "invoices_overdue": len(overdue),
            "average_payment_delay_days": round(avg_delay, 1),
        },
    }


async def refresh_solvency(db: AsyncSession, client: Client) -> None:
    data = await compute_solvency(db, client)
    client.solvency_score = data["score"]
    client.risk_level = data["level"]
    await db.flush()
