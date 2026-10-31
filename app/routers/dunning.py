from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_current_user, get_session
from app.models.client import Client
from app.models.dunning import DunningRun, DunningScenario, DunningStep
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.dunning import (
    DunningScenarioIn,
    DunningScenarioOut,
    GenerateReminderIn,
)
from app.services.ai import generate_dunning_email
from app.services.dunning_engine import (
    execute_due_dunning_runs,
    mark_overdue_invoices,
    start_dunning_for_invoice,
)
from app.services.scheduler import run_daily_jobs

router = APIRouter()


@router.get("/scenarios", response_model=list[DunningScenarioOut])
async def list_scenarios(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return list(
        await db.scalars(
            select(DunningScenario)
            .where(DunningScenario.tenant_id == user.tenant_id)
            .options(selectinload(DunningScenario.steps))
        )
    )


@router.post("/scenarios", response_model=DunningScenarioOut)
async def create_scenario(
    payload: DunningScenarioIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if payload.is_default:
        # Désactive l'ancien défaut
        existing = await db.scalars(
            select(DunningScenario)
            .where(DunningScenario.tenant_id == user.tenant_id)
            .where(DunningScenario.is_default == True)  # noqa: E712
        )
        for s in existing:
            s.is_default = False

    scenario = DunningScenario(
        tenant_id=user.tenant_id,
        name=payload.name,
        description=payload.description,
        is_default=payload.is_default,
    )
    db.add(scenario)
    await db.flush()

    for step in payload.steps:
        db.add(
            DunningStep(
                tenant_id=user.tenant_id,
                scenario_id=scenario.id,
                order_index=step.order_index,
                offset_days=step.offset_days,
                channel=step.channel,
                tone=step.tone,
                subject_template=step.subject_template,
                body_template=step.body_template,
                escalate_to=step.escalate_to,
                locale=step.locale,
            )
        )

    await db.commit()
    scenario = await db.scalar(
        select(DunningScenario)
        .where(DunningScenario.id == scenario.id)
        .options(selectinload(DunningScenario.steps))
    )
    return scenario


@router.post("/generate-reminder")
async def generate_reminder(
    payload: GenerateReminderIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == payload.invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    client = await db.scalar(select(Client).where(Client.id == invoice.client_id))
    tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    from datetime import date as _date

    days = max(0, (_date.today() - invoice.due_date).days)
    result = await generate_dunning_email(
        tone=payload.tone,
        locale=payload.locale or (client.locale if client else "fr_FR"),
        client_name=client.name if client else "Client",
        invoice_number=invoice.number,
        invoice_amount=f"{float(invoice.amount_due):.2f} {invoice.currency}",
        days_overdue=days,
        due_date=invoice.due_date.isoformat(),
        company_name=tenant.name if tenant else "",
        custom_instructions=payload.custom_instructions,
    )
    return result


@router.post("/invoices/{invoice_id}/start")
async def start_run(
    invoice_id: int,
    scenario_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    invoice = await db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == user.tenant_id)
    )
    if not invoice:
        raise HTTPException(404, "Facture introuvable")
    run = await start_dunning_for_invoice(db, invoice, scenario_id)
    await db.commit()
    return {"run_id": run.id, "status": run.status}


@router.post("/runs/execute")
async def execute_runs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    await mark_overdue_invoices(db, user.tenant_id)
    await db.commit()
    stats = await execute_due_dunning_runs(db, user.tenant_id)
    await db.commit()
    return stats


@router.post("/daily")
async def daily_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Tâches quotidiennes : récurrentes, relances. Appeler par cron."""
    return await run_daily_jobs(db, user.tenant_id)
