"""Génération de numéros séquentiels conformes (factures, devis, avoirs).

Le format est configurable par entreprise (tenant) via les champs
`invoice_number_format` / `quote_number_format`, `number_padding` et
`number_reset_yearly`. Placeholders supportés dans le format :
  {YYYY} année (2026), {YY} année 2 chiffres (26), {MM} mois (09),
  {seq} compteur rempli à `number_padding` chiffres (ex. 00010).
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.numbering import NumberingSequence
from app.models.tenant import Tenant

# Formats/préfixes par défaut si le tenant n'a rien de configuré.
DEFAULT_FORMATS = {
    "invoice": "FA-{YYYY}-{seq}",
    "quote": "DV-{YYYY}-{seq}",
    "credit_note": "AV-{YYYY}-{seq}",
}


def render_number(fmt: str, when: date, seq_value: int, padding: int) -> str:
    """Applique un format de numérotation à une valeur de compteur."""
    seq = str(seq_value).zfill(max(padding, 1))
    return (
        fmt.replace("{YYYY}", f"{when.year:04d}")
        .replace("{YY}", f"{when.year % 100:02d}")
        .replace("{MM}", f"{when.month:02d}")
        .replace("{seq}", seq)
    )


async def next_number(db: AsyncSession, tenant_id: int, kind: str, when: date | None = None) -> str:
    """Renvoie le prochain numéro pour ce tenant/kind selon son format configuré."""
    if kind not in DEFAULT_FORMATS:
        raise ValueError(f"Type de numérotation inconnu: {kind}")

    when = when or date.today()
    year = when.year

    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    fmt = getattr(tenant, f"{kind}_number_format", None) or DEFAULT_FORMATS[kind]
    padding = getattr(tenant, "number_padding", None) or 5
    reset_yearly = getattr(tenant, "number_reset_yearly", True)
    if reset_yearly is None:
        reset_yearly = True

    # Compteur remis à zéro chaque année (row par année) ou continu (row year=0).
    seq_year = year if reset_yearly else 0

    seq = await db.scalar(
        select(NumberingSequence)
        .where(NumberingSequence.tenant_id == tenant_id)
        .where(NumberingSequence.kind == kind)
        .where(NumberingSequence.year == seq_year)
        .with_for_update()
    )
    if seq is None:
        seq = NumberingSequence(
            tenant_id=tenant_id, kind=kind, year=seq_year, prefix="", last_value=0, padding=padding
        )
        db.add(seq)
        await db.flush()
        seq = await db.scalar(
            select(NumberingSequence).where(NumberingSequence.id == seq.id).with_for_update()
        )

    seq.last_value += 1
    seq.padding = padding
    await db.flush()

    return render_number(fmt, when, seq.last_value, padding)
