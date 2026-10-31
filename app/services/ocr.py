"""Extraction de données depuis factures fournisseurs via Claude Vision."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from anthropic.types import MessageParam

from app.config import settings
from app.services.ai import claude_json, get_claude

OCR_SCHEMA_HINT = """{
  "supplier_name": "string",
  "supplier_vat": "string|null",
  "document_number": "string|null",
  "document_date": "YYYY-MM-DD|null",
  "due_date": "YYYY-MM-DD|null",
  "currency": "EUR|MAD|USD|...",
  "total_ht": "number|null",
  "total_tax": "number|null",
  "total_ttc": "number|null",
  "line_items": [
    {"description": "string", "quantity": "number", "unit_price": "number", "tax_rate": "number", "amount": "number"}
  ]
}"""


def _file_to_base64(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "application/octet-stream"
    return base64.standard_b64encode(data).decode("ascii"), mime


async def extract_invoice_data(file_path: Path) -> dict:
    """Envoie le fichier à Claude Vision et renvoie un dict structuré."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY manquant — impossible d'utiliser l'OCR Claude.")

    b64, mime = _file_to_base64(file_path)

    if mime.startswith("image/"):
        source = {"type": "base64", "media_type": mime, "data": b64}
        content_block = {"type": "image", "source": source}
    elif mime == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        raise ValueError(f"Type de fichier non supporté pour l'OCR : {mime}")

    prompt = (
        "Tu reçois une facture fournisseur. Extrais les données structurées en JSON "
        "selon le schéma fourni. Si une valeur est absente, mets null. "
        "Pour les montants, renvoie des nombres simples (pas de symbole devise). "
        f"\n\nSchéma attendu :\n{OCR_SCHEMA_HINT}\n\n"
        "Réponds UNIQUEMENT avec du JSON valide, sans markdown."
    )

    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": [
                content_block,
                {"type": "text", "text": prompt},
            ],
        }
    ]

    client = get_claude()
    resp = await client.messages.create(
        model=settings.claude_model_default,
        max_tokens=2000,
        temperature=0.1,
        system="Tu es un OCR expert pour factures et documents administratifs. Tu renvoies uniquement du JSON.",
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
