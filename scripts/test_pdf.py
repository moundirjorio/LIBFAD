"""Test isole de generation PDF (facture + devis demo)."""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.quote import Quote
from app.models.tenant import Tenant
from app.services.pdf import render_pdf, storage_pdf_path


async def main() -> None:
    async with SessionLocal() as db:
        inv = await db.scalar(
            select(Invoice).options(selectinload(Invoice.items)).limit(1)
        )
        if not inv:
            print("Aucune facture en base. Lance scripts.create_demo d'abord.")
            return
        client = await db.scalar(select(Client).where(Client.id == inv.client_id))
        tenant = await db.scalar(select(Tenant).where(Tenant.id == inv.tenant_id))

        out = storage_pdf_path("invoice", inv.tenant_id, inv.number)
        print(f"Generating: {out}")
        path = await render_pdf(
            "pdf/invoice.html",
            {"invoice": inv, "client": client, "tenant": tenant, "settings": None},
            out,
        )
        size = path.stat().st_size
        print(f"[OK] PDF cree : {path} ({size} bytes)")
        if path.suffix == ".html":
            print("[WARN] Fallback HTML utilise (pas vrai PDF)")

        q = await db.scalar(
            select(Quote).options(selectinload(Quote.items)).limit(1)
        )
        if q:
            qclient = await db.scalar(select(Client).where(Client.id == q.client_id))
            qout = storage_pdf_path("quote", q.tenant_id, q.number)
            print(f"Generating: {qout}")
            qpath = await render_pdf(
                "pdf/quote.html",
                {"quote": q, "client": qclient, "tenant": tenant, "settings": None},
                qout,
            )
            print(f"[OK] PDF devis : {qpath} ({qpath.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
