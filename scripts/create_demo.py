"""Crée un tenant + utilisateur de démo + données factices pour tester LIBFAD.

Usage : `python -m scripts.create_demo`
Crée :
  - Tenant "Acme SARL" (FR)
  - User demo@libfad.local / demo1234
  - 3 clients, 5 factures (mélange statuts), 1 devis, 1 abonnement
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models.client import Client
from app.models.invoice import Invoice, InvoiceItem
from app.models.payment import Payment
from app.models.quote import Quote, QuoteItem
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User
from app.security import hash_password
from app.services.billing import compute_document_totals, compute_line
from app.services.numbering import next_number
from scripts.seed_defaults import seed_for_tenant


async def main() -> None:
    async with SessionLocal() as db:
        existing = await db.scalar(select(User).where(User.email == "demo@libfad.local"))
        if existing:
            print(f"Compte démo déjà existant (tenant_id={existing.tenant_id}).")
            return

        tenant = Tenant(
            name="Acme SARL",
            slug="acme-demo",
            legal_name="Acme SARL",
            email="contact@acme.demo",
            phone="+33 1 23 45 67 89",
            siret="12345678900012",
            vat_number="FR12345678900",
            address_line1="1 rue de la Démo",
            postal_code="75001",
            city="Paris",
            country="FR",
            currency="EUR",
            locale="fr_FR",
            iban="FR7612345678901234567890123",
            bank_name="Banque Démo",
            invoice_footer="Acme SARL — RCS Paris 123 456 789 — Capital social 10 000 €",
        )
        db.add(tenant)
        await db.flush()

        user = User(
            tenant_id=tenant.id,
            email="demo@libfad.local",
            password_hash=hash_password("demo1234"),
            full_name="Démo Admin",
            role="owner",
            locale="fr_FR",
        )
        db.add(user)
        await db.flush()

        # Clients
        clients_data = [
            {
                "name": "Bouygues Construction",
                "email": "compta@bouygues.demo",
                "vat_number": "FR98765432101",
                "billing_address_line1": "Challenger - 1 av Eugène Freyssinet",
                "billing_postal_code": "78280",
                "billing_city": "Guyancourt",
                "billing_country": "FR",
                "payment_terms_days": 45,
            },
            {
                "name": "OCP Group",
                "email": "supplier@ocpgroup.demo",
                "ice": "001234567000001",
                "billing_address_line1": "2-4 rue Al Abtal",
                "billing_postal_code": "20200",
                "billing_city": "Casablanca",
                "billing_country": "MA",
                "currency": "MAD",
                "payment_terms_days": 60,
            },
            {
                "name": "Boulangerie du Coin",
                "email": "patron@boulangerie.demo",
                "billing_address_line1": "12 rue du Marché",
                "billing_postal_code": "75011",
                "billing_city": "Paris",
                "billing_country": "FR",
                "payment_terms_days": 15,
            },
        ]
        clients = []
        for cd in clients_data:
            c = Client(tenant_id=tenant.id, **cd)
            db.add(c)
            clients.append(c)
        await db.flush()

        today = date.today()

        # Factures variées
        sample_lines = [
            [("Prestation conseil", Decimal(5), Decimal(800), Decimal(20))],
            [
                ("Développement web", Decimal(20), Decimal(500), Decimal(20)),
                ("Hébergement annuel", Decimal(1), Decimal(120), Decimal(20)),
            ],
            [("Maintenance mensuelle", Decimal(1), Decimal(350), Decimal(20))],
            [("Audit sécurité", Decimal(3), Decimal(900), Decimal(20))],
            [("Formation Python", Decimal(2), Decimal(1200), Decimal(20))],
        ]

        statuses = [
            ("paid", today - timedelta(days=60), 30),
            ("paid", today - timedelta(days=30), 30),
            ("overdue", today - timedelta(days=45), 30),
            ("sent", today - timedelta(days=10), 30),
            ("draft", today, 30),
        ]

        for i, ((status, issued, terms), lines) in enumerate(zip(statuses, sample_lines)):
            number = await next_number(db, tenant.id, "invoice", issued)
            client = clients[i % len(clients)]
            items_data = []
            for desc, qty, price, tax in lines:
                ld = compute_line(qty, price, Decimal(0), tax)
                items_data.append(
                    {
                        "description": desc,
                        "quantity": qty,
                        "unit_price": price,
                        "tax_rate": tax,
                        "discount_rate": Decimal(0),
                        "unit": "u",
                        **ld,
                    }
                )
            totals = compute_document_totals(items_data)
            due = issued + timedelta(days=terms)
            inv = Invoice(
                tenant_id=tenant.id,
                number=number,
                client_id=client.id,
                status=status,
                issue_date=issued,
                due_date=due,
                currency=client.currency or "EUR",
                subtotal_ht=totals["subtotal_ht"],
                total_tax=totals["total_tax"],
                total_ttc=totals["total_ttc"],
                amount_paid=Decimal(0),
                amount_due=totals["total_ttc"],
            )
            db.add(inv)
            await db.flush()
            for j, it_data in enumerate(items_data):
                db.add(InvoiceItem(tenant_id=tenant.id, invoice_id=inv.id, position=j, **it_data))

            if status == "paid":
                pay = Payment(
                    tenant_id=tenant.id,
                    invoice_id=inv.id,
                    paid_on=due - timedelta(days=2),
                    amount=totals["total_ttc"],
                    method="transfer",
                )
                db.add(pay)
                inv.amount_paid = totals["total_ttc"]
                inv.amount_due = Decimal(0)
                inv.paid_at = pay.paid_on

        # Devis
        q_lines = [
            ("Refonte site web", Decimal(40), Decimal(450), Decimal(20)),
            ("Workshop UX", Decimal(2), Decimal(1500), Decimal(20)),
        ]
        items_data = []
        for desc, qty, price, tax in q_lines:
            ld = compute_line(qty, price, Decimal(0), tax)
            items_data.append({"description": desc, "quantity": qty, "unit_price": price, "tax_rate": tax, "unit": "u", "discount_rate": Decimal(0), **ld})
        totals = compute_document_totals(items_data)
        q_number = await next_number(db, tenant.id, "quote", today)
        quote = Quote(
            tenant_id=tenant.id,
            number=q_number,
            client_id=clients[0].id,
            status="sent",
            issue_date=today,
            valid_until=today + timedelta(days=30),
            currency="EUR",
            subtotal_ht=totals["subtotal_ht"],
            total_tax=totals["total_tax"],
            total_ttc=totals["total_ttc"],
        )
        db.add(quote)
        await db.flush()
        for k, it_data in enumerate(items_data):
            db.add(QuoteItem(tenant_id=tenant.id, quote_id=quote.id, position=k, **it_data))

        # Abonnement
        sub = Subscription(
            tenant_id=tenant.id,
            client_id=clients[2].id,
            name="Maintenance mensuelle",
            period="monthly",
            interval=1,
            amount_ht=Decimal("350"),
            tax_rate=Decimal("20"),
            currency="EUR",
            start_date=today - timedelta(days=60),
            next_billing_date=today + timedelta(days=5),
        )
        db.add(sub)

        await db.commit()

        # Données par défaut (TVA, scénarios)
        await seed_for_tenant(tenant.id)

        print(f"✔ Démo créée. Login : demo@libfad.local / demo1234 (tenant_id={tenant.id})")


if __name__ == "__main__":
    asyncio.run(main())
