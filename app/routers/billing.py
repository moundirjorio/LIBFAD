"""Abonnement SaaS : changement d'offre et paiement (Stripe).

Le paiement récurrent nécessite une clé Stripe (STRIPE_SECRET_KEY) + les IDs de
prix (STRIPE_PRICE_PRO / STRIPE_PRICE_BUSINESS). Tant que ce n'est pas configuré,
l'endpoint renvoie un message clair au lieu d'échouer silencieusement.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_user, get_session
from app.models.tenant import Tenant
from app.models.user import User
from app.services.plans import PLANS

router = APIRouter()

_PRICE_IDS = {
    "pro": lambda: settings.stripe_price_pro,
    "business": lambda: settings.stripe_price_business,
}


class CheckoutIn(BaseModel):
    plan: str


@router.post("/checkout")
async def create_checkout(
    payload: CheckoutIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if payload.plan not in PLANS:
        raise HTTPException(400, "Offre inconnue.")
    if payload.plan == "free":
        # Rétrograder vers l'offre gratuite : immédiat, sans paiement.
        tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
        tenant.plan = "free"
        tenant.plan_status = "active"
        await db.commit()
        return {"downgraded": True}

    if not settings.stripe_configured:
        raise HTTPException(
            status_code=400,
            detail="Le paiement en ligne n'est pas encore configuré (clé Stripe manquante). "
            "Contacte-nous pour changer d'offre.",
        )

    # --- Chemin Stripe (activé quand STRIPE_SECRET_KEY est renseignée) ---
    price_id = _PRICE_IDS.get(payload.plan, lambda: "")()
    if not price_id:
        raise HTTPException(400, f"ID de prix Stripe manquant pour l'offre {payload.plan}.")

    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    try:
        import stripe

        stripe.api_key = settings.stripe_secret_key
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            customer=tenant.stripe_customer_id or None,
            customer_email=None if tenant.stripe_customer_id else user.email,
            client_reference_id=str(tenant.id),
            success_url=f"{settings.base_url.rstrip('/')}/billing?checkout=success",
            cancel_url=f"{settings.base_url.rstrip('/')}/billing?checkout=cancel",
            metadata={"tenant_id": tenant.id, "plan": payload.plan},
        )
        return {"checkout_url": session.url}
    except ImportError:
        raise HTTPException(500, "Le paquet 'stripe' n'est pas installé (pip install stripe).") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Erreur Stripe : {exc}") from exc
