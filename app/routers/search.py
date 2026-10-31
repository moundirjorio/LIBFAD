"""Recherche globale (palette de commandes Ctrl+K) : clients, factures, devis."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.quote import Quote
from app.models.user import User

router = APIRouter()


@router.get("")
async def search(
    q: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": []}
    like = f"%{q}%"
    tid = user.tenant_id
    results: list[dict] = []

    for c in await db.scalars(
        select(Client).where(Client.tenant_id == tid, Client.name.ilike(like)).order_by(Client.name).limit(6)
    ):
        results.append({"type": "Client", "icon": "user", "label": c.name,
                        "sub": c.email or c.billing_city or "", "url": f"/clients/{c.id}"})

    for i in await db.scalars(
        select(Invoice).where(Invoice.tenant_id == tid, Invoice.number.ilike(like))
        .order_by(Invoice.issue_date.desc()).limit(6)
    ):
        results.append({"type": "Facture", "icon": "receipt-text", "label": i.number,
                        "sub": f"{i.total_ttc} {i.currency}", "url": f"/invoices/{i.id}"})

    for qo in await db.scalars(
        select(Quote).where(Quote.tenant_id == tid, Quote.number.ilike(like))
        .order_by(Quote.issue_date.desc()).limit(6)
    ):
        results.append({"type": "Devis", "icon": "file-check-2", "label": qo.number,
                        "sub": f"{qo.total_ttc} {qo.currency}", "url": f"/quotes/{qo.id}"})

    return {"results": results}
