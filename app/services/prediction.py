"""Prédiction simple des retards de paiement et détection d'anomalies."""

from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice


async def predict_payment_delay(db: AsyncSession, tenant_id: int, client_id: int) -> dict:
    """Prédit le délai probable de paiement (en jours) du prochain règlement.

    Renvoie {expected_delay_days, confidence, sample_size}.
    """
    invoices = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id)
            .where(Invoice.client_id == client_id)
            .where(Invoice.status == "paid")
            .where(Invoice.paid_at.is_not(None))
        )
    )
    delays = [
        (inv.paid_at - inv.due_date).days
        for inv in invoices
        if inv.paid_at and inv.due_date
    ]
    if not delays:
        return {"expected_delay_days": None, "confidence": "low", "sample_size": 0}

    mean = statistics.mean(delays)
    stdev = statistics.pstdev(delays) if len(delays) > 1 else 0
    confidence = "high" if len(delays) >= 10 else ("medium" if len(delays) >= 4 else "low")
    return {
        "expected_delay_days": round(mean, 1),
        "stdev": round(stdev, 1),
        "confidence": confidence,
        "sample_size": len(delays),
    }


async def detect_anomalies(db: AsyncSession, tenant_id: int) -> list[dict]:
    """Détecte les factures dont le montant s'écarte fortement de la moyenne du client."""
    rows = list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id)
            .where(Invoice.status.in_(["issued", "sent", "paid", "partially_paid", "overdue"]))
        )
    )
    by_client: dict[int, list[Invoice]] = {}
    for inv in rows:
        by_client.setdefault(inv.client_id, []).append(inv)

    anomalies: list[dict] = []
    for client_id, invs in by_client.items():
        amounts = [float(i.total_ttc) for i in invs if i.total_ttc]
        if len(amounts) < 4:
            continue
        mean = statistics.mean(amounts)
        stdev = statistics.pstdev(amounts) or 1
        for inv in invs:
            amt = float(inv.total_ttc or 0)
            z = abs(amt - mean) / stdev if stdev else 0
            if z > 2.5:
                anomalies.append(
                    {
                        "invoice_id": inv.id,
                        "number": inv.number,
                        "client_id": client_id,
                        "amount": amt,
                        "mean_amount": round(mean, 2),
                        "z_score": round(z, 2),
                    }
                )
    return anomalies
