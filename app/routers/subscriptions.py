from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_session
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.subscription import SubscriptionIn, SubscriptionOut

router = APIRouter()


def _to_decimal(value: str | None, default: Decimal = Decimal(0)) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return list(
        await db.scalars(select(Subscription).where(Subscription.tenant_id == user.tenant_id))
    )


@router.post("", response_model=SubscriptionOut)
async def create_subscription(
    payload: SubscriptionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    start = payload.start_date or date.today()
    sub = Subscription(
        tenant_id=user.tenant_id,
        client_id=payload.client_id,
        product_id=payload.product_id,
        name=payload.name,
        description=payload.description,
        period=payload.period,
        interval=payload.interval,
        amount_ht=payload.amount_ht,
        tax_rate=payload.tax_rate,
        currency=payload.currency,
        start_date=start,
        end_date=payload.end_date,
        next_billing_date=payload.next_billing_date or start,
        auto_send=payload.auto_send,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


@router.delete("/{sub_id}")
async def delete_subscription(
    sub_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    sub = await db.scalar(
        select(Subscription).where(Subscription.id == sub_id, Subscription.tenant_id == user.tenant_id)
    )
    if not sub:
        raise HTTPException(404, "Abonnement introuvable")
    await db.delete(sub)
    await db.commit()
    return {"deleted": True}


# --- HTML form endpoints ----------------------------------------------------
@router.post("/form")
async def create_subscription_form(
    client_id: int = Form(...),
    name: str = Form(...),
    amount_ht: str = Form(...),
    product_id: int | None = Form(None),
    description: str | None = Form(None),
    period: str = Form("monthly"),
    interval: int = Form(1),
    tax_rate: str = Form("0"),
    currency: str = Form("EUR"),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    auto_send: bool = Form(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    start = _to_date(start_date) or date.today()
    sub = Subscription(
        tenant_id=user.tenant_id,
        client_id=client_id,
        product_id=product_id,
        name=name,
        description=description or None,
        period=period,
        interval=interval,
        amount_ht=_to_decimal(amount_ht),
        tax_rate=_to_decimal(tax_rate),
        currency=currency,
        start_date=start,
        end_date=_to_date(end_date),
        next_billing_date=start,
        auto_send=auto_send,
    )
    db.add(sub)
    await db.commit()
    return RedirectResponse(url="/subscriptions", status_code=303)


@router.post("/{sub_id}/delete-form")
async def delete_subscription_form(
    sub_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    sub = await db.scalar(
        select(Subscription).where(Subscription.id == sub_id, Subscription.tenant_id == user.tenant_id)
    )
    if sub:
        await db.delete(sub)
        await db.commit()
    return RedirectResponse(url="/subscriptions", status_code=303)
