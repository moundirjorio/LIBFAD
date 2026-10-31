"""Régénère un PDF de facture avec le template actuel (debug)."""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import SessionLocal
from app.models.invoice import Invoice
from app.models.client import Client
from app.models.tenant import Tenant
from app.services.pdf import render_pdf, storage_pdf_path


async def main(number: str):
    async with SessionLocal() as db:
        inv = await db.scalar(
            select(Invoice).where(Invoice.number == number).options(selectinload(Invoice.items))
        )
        if not inv:
            print("Facture introuvable:", number)
            return
        client = await db.scalar(select(Client).where(Client.id == inv.client_id))
        tenant = await db.scalar(select(Tenant).where(Tenant.id == inv.tenant_id))
        out = storage_pdf_path("invoice", inv.tenant_id, inv.number)
        res = await render_pdf(
            "pdf/invoice.html",
            {"invoice": inv, "client": client, "tenant": tenant, "settings": settings},
            out,
        )
        print("Rendu ->", res)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "FA-2026-00001"))
