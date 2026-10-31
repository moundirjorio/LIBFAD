"""Génération de PDFs depuis les templates Jinja2.

Utilise xhtml2pdf (pure Python, fonctionne sur Windows sans GTK).
Fallback HTML si la lib n'est pas dispo.
"""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.i18n import amount, amount_in_words, date_fr, money, number

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Couleur de marque par défaut si le tenant n'en définit pas.
DEFAULT_BRAND_COLOR = "#4F46E5"

# Libellés bilingues des documents PDF (factures / devis). La langue est
# déduite du champ `locale` du document ; tout ce qui commence par "en"
# bascule le PDF en anglais, sinon français par défaut.
PDF_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "date": "Date",
        "date_fmt": "%d/%m/%Y",
        "invoice_number": "Numéro de facture",
        "quote_number": "Numéro de devis",
        "valid_until": "Valable jusqu'au",
        "client": "Client :",
        "ice": "ICE :",
        "vat": "TVA :",
        "subject": "Objet :",
        "subject_invoice": "Prestation de services",
        "subject_quote": "Proposition commerciale",
        "col_desc": "Description",
        "col_unit": "Unité",
        "col_qty": "Quantité",
        "col_pu": "Prix Unitaire<br/>HT",
        "col_total": "Total HT",
        "total_ht": "Montant Total HT",
        "total_vat": "Total TVA",
        "total_ttc": "Montant Total TTC",
        "words_invoice": "Arrêté la présente facture à la somme de :",
        "words_quote": "Arrêté le présent devis à la somme de :",
        "approval": "Bon pour accord : date, signature et cachet du client",
        "rc": "N° RC",
        "siret": "SIRET",
        "doc_invoice": "FACTURE",
        "doc_quote": "DEVIS",
        "issuer": "ÉMETTEUR",
        "billed_to": "FACTURÉ À",
        "details": "DÉTAILS",
        "issued": "Émise",
        "due": "Échéance",
        "currency": "Devise",
        "qty_short": "Qté",
        "pu_short": "PU HT",
        "vat_short": "TVA",
        "paid": "Déjà payé",
        "remaining": "Reste dû",
        "notes": "Notes",
        "payment_transfer": "Règlement par virement",
        "attention_to": "À L'ATTENTION DE",
        "conditions": "Conditions",
        "penalty": (
            "En cas de retard de paiement, application d'une pénalité égale à 3 fois "
            "le taux d'intérêt légal. Pas d'escompte pour paiement anticipé."
        ),
    },
    "en": {
        "date": "Date",
        "date_fmt": "%d/%m/%Y",
        "invoice_number": "Invoice No.",
        "quote_number": "Quote No.",
        "valid_until": "Valid until",
        "client": "Bill to:",
        "ice": "ICE:",
        "vat": "VAT:",
        "subject": "Subject:",
        "subject_invoice": "Services rendered",
        "subject_quote": "Commercial proposal",
        "col_desc": "Description",
        "col_unit": "Unit",
        "col_qty": "Qty",
        "col_pu": "Unit Price<br/>(excl. tax)",
        "col_total": "Total (excl. tax)",
        "total_ht": "Total (excl. tax)",
        "total_vat": "Total VAT",
        "total_ttc": "Total (incl. tax)",
        "words_invoice": "Total amount due:",
        "words_quote": "Total amount:",
        "approval": "Approved: date, signature and company stamp",
        "rc": "Trade Reg. No.",
        "siret": "SIRET",
        "doc_invoice": "INVOICE",
        "doc_quote": "QUOTE",
        "issuer": "FROM",
        "billed_to": "BILL TO",
        "details": "DETAILS",
        "issued": "Issued",
        "due": "Due",
        "currency": "Currency",
        "qty_short": "Qty",
        "pu_short": "Unit price",
        "vat_short": "VAT",
        "paid": "Paid",
        "remaining": "Balance due",
        "notes": "Notes",
        "payment_transfer": "Payment by bank transfer",
        "attention_to": "ATTENTION",
        "conditions": "Terms",
        "penalty": (
            "Late payments are subject to a penalty of three times the legal interest "
            "rate. No early-payment discount."
        ),
    },
}


def _doc_lang(context: dict) -> str:
    """Déduit la langue du document (facture/devis) depuis son locale."""
    doc = context.get("invoice") or context.get("quote")
    loc = str(getattr(doc, "locale", "") or "").lower() if doc is not None else ""
    return "en" if loc.startswith("en") else "fr"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=False,
)
_env.filters["money"] = money
_env.filters["num"] = number
_env.filters["date_fr"] = date_fr
_env.filters["in_words"] = amount_in_words
_env.filters["amount"] = amount


def logo_data_uri(tenant) -> str | None:
    """Renvoie le logo de la société encodé en data URI base64.

    xhtml2pdf ne résout pas les chemins/URL externes de façon fiable :
    embarquer l'image en base64 garantit qu'elle apparaît dans le PDF.
    """
    path = getattr(tenant, "logo_path", None)
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = settings.storage_path / path
    if not p.exists():
        return None
    raw = p.read_bytes()
    # Vérifie que l'image est décodable : un fichier corrompu ferait échouer
    # tout le rendu PDF (reportlab/PIL). Mieux vaut omettre le logo.
    try:
        from PIL import Image

        Image.open(io.BytesIO(raw)).verify()
    except Exception:
        return None
    mime, _ = mimetypes.guess_type(str(p))
    if mime not in ("image/png", "image/jpeg", "image/gif"):
        mime = "image/png"
    data = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{data}"


# Modèles de document disponibles. La société choisit "classic" ou "modern"
# dans ses paramètres (champ tenant.pdf_template) ; chaque document commercial
# est rendu avec le fichier correspondant.
_TEMPLATE_VARIANTS: dict[str, dict[str, str]] = {
    "pdf/invoice.html": {"classic": "pdf/invoice.html", "modern": "pdf/invoice_modern.html"},
    "pdf/quote.html": {"classic": "pdf/quote.html", "modern": "pdf/quote_modern.html"},
}


def resolve_template(template: str, tenant) -> str:
    """Choisit le fichier de template selon le modèle PDF de la société."""
    variants = _TEMPLATE_VARIANTS.get(template)
    if not variants or tenant is None:
        return template
    choice = getattr(tenant, "pdf_template", None) or "classic"
    return variants.get(choice, template)


def _render_html(template: str, context: dict) -> str:
    # Injection automatique des éléments de marque + libellés de langue.
    context = dict(context)
    lang = _doc_lang(context)
    context.setdefault("lang", lang)
    context.setdefault("t", PDF_LABELS[lang])
    tenant = context.get("tenant")
    if tenant is not None:
        context.setdefault("logo_data_uri", logo_data_uri(tenant))
        context.setdefault(
            "brand_color", getattr(tenant, "primary_color", None) or DEFAULT_BRAND_COLOR
        )
        template = resolve_template(template, tenant)
    return _env.get_template(template).render(**context)


def _write_pdf_sync(html: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from xhtml2pdf import pisa

        with open(output_path, "wb") as fh:
            pisa_status = pisa.CreatePDF(html, dest=fh, encoding="utf-8")
        if pisa_status.err:
            raise RuntimeError(f"xhtml2pdf a renvoyé {pisa_status.err} erreurs")
        return output_path
    except Exception:
        # Fallback : on écrit au moins le HTML pour ne pas perdre le contenu
        fallback = output_path.with_suffix(".html")
        fallback.write_text(html, encoding="utf-8")
        return fallback


async def render_pdf(template: str, context: dict, output_path: Path) -> Path:
    html = _render_html(template, context)
    return await asyncio.to_thread(_write_pdf_sync, html, output_path)


def storage_pdf_path(kind: str, tenant_id: int, number: str) -> Path:
    return settings.storage_path / "pdfs" / kind / f"tenant_{tenant_id}" / f"{number}.pdf"
