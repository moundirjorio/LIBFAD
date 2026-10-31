"""Insère les données par défaut pour un tenant (TVA, scénarios de relance, templates).

Usage : `python -m scripts.seed_defaults --tenant-id 1`
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models.dunning import DunningScenario, DunningStep
from app.models.email_template import EmailTemplate
from app.models.tax_rate import TaxRate
from app.models.tenant import Tenant


DEFAULT_TAX_RATES_FR = [
    ("TVA 20% (taux normal)", Decimal("20"), "FR", True),
    ("TVA 10% (taux intermédiaire)", Decimal("10"), "FR", False),
    ("TVA 5.5% (taux réduit)", Decimal("5.5"), "FR", False),
    ("TVA 2.1% (taux super-réduit)", Decimal("2.1"), "FR", False),
    ("TVA 0% (exonération)", Decimal("0"), "FR", False),
]

DEFAULT_TAX_RATES_MA = [
    ("TVA 20% (Maroc normal)", Decimal("20"), "MA", True),
    ("TVA 14% (Maroc)", Decimal("14"), "MA", False),
    ("TVA 10% (Maroc réduit)", Decimal("10"), "MA", False),
    ("TVA 7% (Maroc)", Decimal("7"), "MA", False),
    ("TVA 0%", Decimal("0"), "MA", False),
]


def _dunning_steps() -> list[dict]:
    return [
        {
            "order_index": 0,
            "offset_days": 3,
            "channel": "email",
            "tone": "polite",
            "subject_template": None,
            "body_template": None,
        },
        {
            "order_index": 1,
            "offset_days": 14,
            "channel": "email",
            "tone": "firm",
        },
        {
            "order_index": 2,
            "offset_days": 30,
            "channel": "email",
            "tone": "final",
            "escalate_to": "accounting",
        },
    ]


DEFAULT_EMAIL_TEMPLATES = [
    {
        "code": "invoice_sent",
        "locale": "fr_FR",
        "subject": "Facture {{invoice_number}} — {{company_name}}",
        "body": (
            "Bonjour,\n\n"
            "Veuillez trouver ci-jointe la facture {{invoice_number}} "
            "d'un montant de {{amount}} {{currency}}.\n\n"
            "Échéance : {{due_date}}.\n\n"
            "Cordialement,\n{{company_name}}"
        ),
    },
    {
        "code": "quote_sent",
        "locale": "fr_FR",
        "subject": "Votre devis {{quote_number}}",
        "body": (
            "Bonjour,\n\n"
            "Veuillez trouver ci-joint notre devis {{quote_number}}, "
            "valable jusqu'au {{valid_until}}.\n\n"
            "Restant à votre disposition,\n{{company_name}}"
        ),
    },
]


async def seed_for_tenant(tenant_id: int) -> None:
    async with SessionLocal() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if not tenant:
            raise SystemExit(f"Tenant {tenant_id} introuvable.")

        # Tax rates
        country = tenant.country or "FR"
        rates = DEFAULT_TAX_RATES_MA if country == "MA" else DEFAULT_TAX_RATES_FR
        for name, rate, cn, is_default in rates:
            exists = await db.scalar(
                select(TaxRate)
                .where(TaxRate.tenant_id == tenant_id)
                .where(TaxRate.name == name)
            )
            if not exists:
                db.add(
                    TaxRate(
                        tenant_id=tenant_id,
                        name=name,
                        rate=rate,
                        country=cn,
                        is_default=is_default,
                    )
                )

        # Scénario de relance par défaut
        existing = await db.scalar(
            select(DunningScenario)
            .where(DunningScenario.tenant_id == tenant_id)
            .where(DunningScenario.is_default == True)  # noqa: E712
        )
        if not existing:
            scenario = DunningScenario(
                tenant_id=tenant_id,
                name="Scénario standard",
                description="3 relances : J+3 polie, J+14 ferme, J+30 mise en demeure",
                is_default=True,
                is_active=True,
            )
            db.add(scenario)
            await db.flush()
            for step_data in _dunning_steps():
                db.add(DunningStep(tenant_id=tenant_id, scenario_id=scenario.id, **step_data))

        # Templates d'emails
        for tmpl in DEFAULT_EMAIL_TEMPLATES:
            exists = await db.scalar(
                select(EmailTemplate)
                .where(EmailTemplate.tenant_id == tenant_id)
                .where(EmailTemplate.code == tmpl["code"])
                .where(EmailTemplate.locale == tmpl["locale"])
            )
            if not exists:
                db.add(EmailTemplate(tenant_id=tenant_id, **tmpl))

        await db.commit()
        print(f"✔ Données par défaut insérées pour le tenant {tenant.name} (#{tenant_id}).")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", type=int, required=True, help="ID du tenant à initialiser")
    args = parser.parse_args()
    await seed_for_tenant(args.tenant_id)


if __name__ == "__main__":
    asyncio.run(main())
