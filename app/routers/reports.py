"""API rapports & analytics (JSON pour graphiques + exports Excel)."""

from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.deps import get_current_user, get_session
from app.models.user import User
from app.services.exports import to_xlsx
from app.services.prediction import detect_anomalies, predict_payment_delay
from app.services.reports import aged_balance, revenue_summary, top_clients

from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MONTHS_FR = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
              "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]


@router.get("/revenue")
async def get_revenue(
    year: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await revenue_summary(db, user.tenant_id, year)


@router.get("/aged-balance")
async def get_aged_balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await aged_balance(db, user.tenant_id)


@router.get("/top-clients")
async def get_top_clients(
    limit: int = 10,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await top_clients(db, user.tenant_id, limit)


@router.get("/anomalies")
async def get_anomalies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await detect_anomalies(db, user.tenant_id)


@router.get("/clients/{client_id}/payment-delay")
async def get_payment_delay(
    client_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await predict_payment_delay(db, user.tenant_id, client_id)


# --- Exports Excel ----------------------------------------------------------
@router.get("/export/revenue.xlsx")
async def export_revenue(
    year: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    summary = await revenue_summary(db, user.tenant_id, year)
    rows = [[_MONTHS_FR[i], summary["monthly"][i]] for i in range(12)]
    rows.append(["Total", summary["total_revenue"]])
    content = to_xlsx(rows, ["Mois", "Chiffre d'affaires TTC"], sheet_name=f"CA {summary['year']}")
    return Response(
        content=content,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="ca_{summary["year"]}.xlsx"'},
    )


@router.get("/export/top-clients.xlsx")
async def export_top_clients(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    clients = await top_clients(db, user.tenant_id, limit)
    rows = [[c["name"], c["revenue"], c["invoice_count"]] for c in clients]
    content = to_xlsx(rows, ["Client", "CA TTC", "Nb factures"], sheet_name="Top clients")
    today = date.today().isoformat()
    return Response(
        content=content,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="top_clients_{today}.xlsx"'},
    )
