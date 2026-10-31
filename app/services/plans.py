"""Plans d'abonnement SaaS et application des quotas.

Les offres sont définies ici (contrôlées par l'éditeur, pas par le tenant).
`None` = illimité. Prix en devise de facturation de LIBFAD (MAD/mois HT).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.client import Client
from app.models.invoice import Invoice


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price_month: int  # MAD/mois
    max_clients: int | None
    max_invoices_per_month: int | None
    features: tuple[str, ...]


PLANS: dict[str, Plan] = {
    "free": Plan(
        code="free", name="Gratuit", price_month=0,
        max_clients=5, max_invoices_per_month=10,
        features=("Facturation & devis", "Portail client", "1 utilisateur"),
    ),
    "pro": Plan(
        code="pro", name="Pro", price_month=149,
        max_clients=None, max_invoices_per_month=None,
        features=("Clients & factures illimités", "Relances automatiques", "Assistant IA", "5 utilisateurs"),
    ),
    "business": Plan(
        code="business", name="Business", price_month=349,
        max_clients=None, max_invoices_per_month=None,
        features=("Tout Pro", "Utilisateurs illimités", "Multi-sociétés", "Support prioritaire"),
    ),
}

DEFAULT_PLAN = "free"


def get_plan(tenant) -> Plan:
    return PLANS.get(getattr(tenant, "plan", None) or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def is_in_trial(tenant) -> bool:
    ends = getattr(tenant, "trial_ends_at", None)
    return (
        getattr(tenant, "plan_status", None) == "trialing"
        and ends is not None
        and ends > utcnow()
    )


def effective_limits(tenant) -> tuple[int | None, int | None]:
    """(max_clients, max_invoices_per_month) applicables. Illimité pendant l'essai."""
    if is_in_trial(tenant):
        return (None, None)
    plan = get_plan(tenant)
    return (plan.max_clients, plan.max_invoices_per_month)


async def usage(db: AsyncSession, tenant_id: int) -> dict:
    """Consommation courante du tenant (clients, factures du mois)."""
    n_clients = await db.scalar(
        select(func.count(Client.id)).where(Client.tenant_id == tenant_id)
    )
    today = date.today()
    n_invoices_month = await db.scalar(
        select(func.count(Invoice.id)).where(
            Invoice.tenant_id == tenant_id,
            func.extract("year", Invoice.issue_date) == today.year,
            func.extract("month", Invoice.issue_date) == today.month,
        )
    )
    return {"clients": n_clients or 0, "invoices_month": n_invoices_month or 0}


async def enforce_quota(db: AsyncSession, tenant, resource: str) -> None:
    """Lève 402 si la création d'une ressource dépasse le quota du plan."""
    max_clients, max_invoices = effective_limits(tenant)
    use = await usage(db, tenant.id)
    if resource == "client" and max_clients is not None and use["clients"] >= max_clients:
        raise HTTPException(
            status_code=402,
            detail=f"Limite du plan {get_plan(tenant).name} atteinte ({max_clients} clients). "
            "Passez à un plan supérieur pour en ajouter davantage.",
        )
    if resource == "invoice" and max_invoices is not None and use["invoices_month"] >= max_invoices:
        raise HTTPException(
            status_code=402,
            detail=f"Limite du plan {get_plan(tenant).name} atteinte "
            f"({max_invoices} factures/mois). Passez à un plan supérieur.",
        )
