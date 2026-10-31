"""Client Claude (Anthropic) + helpers métier IA."""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from app.config import settings

_client: AsyncAnthropic | None = None


def get_claude() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY manquant — définis-le dans .env pour activer les fonctionnalités IA."
            )
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def claude_complete(
    messages: list[MessageParam],
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.4,
    heavy: bool = False,
) -> dict[str, Any]:
    client = get_claude()
    target_model = model or (settings.claude_model_heavy if heavy else settings.claude_model_default)
    resp = await client.messages.create(
        model=target_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "Tu es un assistant administratif et financier expert.",
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return {
        "text": text,
        "model": target_model,
        "input_tokens": resp.usage.input_tokens if resp.usage else None,
        "output_tokens": resp.usage.output_tokens if resp.usage else None,
    }


async def claude_json(
    prompt: str,
    *,
    system: str | None = None,
    schema_hint: str | None = None,
    model: str | None = None,
    heavy: bool = False,
) -> dict:
    """Demande une réponse JSON stricte (parse robuste)."""
    full_prompt = prompt
    if schema_hint:
        full_prompt += (
            "\n\nRéponds STRICTEMENT en JSON valide (rien d'autre, pas de markdown), "
            "selon ce schéma :\n" + schema_hint
        )
    result = await claude_complete(
        messages=[{"role": "user", "content": full_prompt}],
        system=system or "Tu es un assistant qui renvoie uniquement du JSON valide.",
        model=model,
        heavy=heavy,
        temperature=0.1,
    )
    text = result["text"].strip()
    # Strip code fences si présents
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Tente d'extraire le premier objet JSON {} dans le texte
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


# ---- Helpers métier ----------------------------------------------------------

DUNNING_SYSTEM = (
    "Tu es un assistant administratif spécialisé dans la relance client. "
    "Tu rédiges des emails professionnels, factuels, polis et adaptés au ton demandé. "
    "Tu n'inventes JAMAIS de montants, dates ou numéros : utilise uniquement ceux fournis."
)


async def generate_dunning_email(
    *,
    tone: str,
    locale: str,
    client_name: str,
    invoice_number: str,
    invoice_amount: str,
    days_overdue: int,
    due_date: str,
    company_name: str,
    company_signature: str | None = None,
    custom_instructions: str | None = None,
) -> dict:
    """Génère sujet + corps d'email de relance."""
    extra = f"\nInstructions complémentaires : {custom_instructions}" if custom_instructions else ""
    prompt = f"""Génère un email de relance pour une facture impayée.

Contexte :
- Ton : {tone} (polite, firm, final ou legal)
- Langue : {locale}
- Client : {client_name}
- Facture : {invoice_number}
- Montant dû : {invoice_amount}
- Date d'échéance : {due_date}
- Retard : {days_overdue} jours
- Notre entreprise : {company_name}
- Signature : {company_signature or company_name}{extra}

Renvoie un JSON avec deux clés : "subject" (sujet court) et "body" (corps de l'email, multi-lignes, avec formule de politesse et signature)."""
    return await claude_json(
        prompt,
        system=DUNNING_SYSTEM,
        schema_hint='{"subject": "string", "body": "string"}',
    )


CHAT_SYSTEM = (
    "Tu es l'assistant administratif LIBFAD. Tu aides l'utilisateur à gérer ses factures, "
    "clients, paiements, relances et reporting. Tu réponds en français par défaut, de manière "
    "concise, précise et actionnable. Si on te fournit des données métier (factures, clients), "
    "tu t'appuies dessus pour répondre."
)


async def chat_reply(
    history: list[MessageParam],
    *,
    business_context: str | None = None,
) -> dict:
    system = CHAT_SYSTEM
    if business_context:
        system += "\n\nContexte métier (données du tenant) :\n" + business_context
    return await claude_complete(history, system=system, temperature=0.5, max_tokens=1500)
