"""Agent conversationnel LIBFAD : Claude + outils métier.

Utilisé par le bot Telegram pour comprendre les demandes en langage naturel
(« combien me doit Bouygues ? », « crée une facture de 3 jours à 4000 pour OCP »).
Les outils sont les actions de `assistant_actions`, donc strictement les mêmes
règles et le même cloisonnement par société que l'interface.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from datetime import date

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.tenant import Tenant
from app.models.user import User
from app.services import assistant_actions as actions
from app.services.ai import get_claude

logger = logging.getLogger("libfad.agent")

MAX_TURNS = 8                  # allers-retours outils par message utilisateur
CONVERSATION_TTL = 30 * 60     # une conversation inactive 30 min repart de zéro
CONVERSATION_MAX_MESSAGES = 60


class AgentUnavailable(Exception):
    """L'IA ne peut pas répondre (clé absente, crédit épuisé, panne réseau…)."""


_LINES_SCHEMA = {
    "type": "array",
    "description": "Lignes du document.",
    "items": {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "quantite": {"type": "number", "description": "Quantité (défaut 1)."},
            "prix_unitaire_ht": {"type": "number", "description": "Prix unitaire hors taxes."},
            "tva_pct": {"type": "number", "description": "Taux de TVA en % (ex. 20). 0 si non précisé."},
        },
        "required": ["description", "prix_unitaire_ht"],
    },
}


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": required or []}}


TOOLS: list[dict] = [
    _tool("resume_activite",
          "Indicateurs clés de la société : facturé, encaissé, reste à encaisser, retards, trésorerie, "
          "devis en cours, achats du mois. Les montants de factures sont ventilés par devise."),
    _tool("lister_factures",
          "Liste les factures de vente, de la plus récente à la plus ancienne.",
          {"statut": {"type": "string", "enum": ["impayees", "en_retard", "payees", "brouillons", "toutes"]},
           "client": {"type": "string", "description": "Nom (même partiel) du client, pour filtrer."},
           "limit": {"type": "integer", "description": "Nombre maximum de factures (défaut 10, max 30)."}}),
    _tool("detail_facture", "Détail complet d'une facture : lignes, totaux, payé, reste dû.",
          {"numero": {"type": "string", "description": "Numéro de facture, ou sa fin (ex. 00008)."}}, ["numero"]),
    _tool("impayes_par_client", "Factures impayées regroupées par client, avec le total dû par client."),
    _tool("lister_clients", "Liste les clients, avec recherche optionnelle sur le nom.",
          {"recherche": {"type": "string"}}),
    _tool("lister_devis", "Liste les derniers devis."),
    _tool("lister_fournisseurs", "Liste les fournisseurs.", {"recherche": {"type": "string"}}),
    _tool("lister_achats", "Derniers reçus / factures d'achat et total des achats du mois."),
    _tool("tresorerie", "Comptes bancaires, soldes, derniers mouvements, encaissements à rapprocher."),
    _tool("creer_client", "Crée une fiche client.",
          {"nom": {"type": "string"}, "email": {"type": "string"}, "telephone": {"type": "string"},
           "ice": {"type": "string"}, "ville": {"type": "string"},
           "pays": {"type": "string", "description": "Code pays ISO à 2 lettres (MA, FR…)."}}, ["nom"]),
    _tool("creer_fournisseur", "Crée une fiche fournisseur.",
          {"nom": {"type": "string"}, "email": {"type": "string"}, "telephone": {"type": "string"},
           "ice": {"type": "string"},
           "compte": {"type": "string", "description": "Compte comptable d'achat par défaut (ex. 6111)."}}, ["nom"]),
    _tool("creer_facture",
          "Crée une facture de vente pour un client existant. Elle est créée en brouillon, sauf si "
          "`emettre` vaut true (elle devient alors due et apparaît dans les impayés).",
          {"client": {"type": "string", "description": "Nom (même partiel) du client."},
           "lignes": _LINES_SCHEMA,
           "devise": {"type": "string", "description": "MAD, EUR ou USD. Par défaut : devise du client."},
           "langue": {"type": "string", "enum": ["fr", "en"], "description": "Langue du PDF."},
           "delai_jours": {"type": "integer", "description": "Délai de paiement en jours (défaut 30)."},
           "objet": {"type": "string", "description": "Objet affiché sur la facture."},
           "emettre": {"type": "boolean"}}, ["client", "lignes"]),
    _tool("creer_devis", "Crée un devis pour un client existant.",
          {"client": {"type": "string"}, "lignes": _LINES_SCHEMA, "devise": {"type": "string"},
           "langue": {"type": "string", "enum": ["fr", "en"]},
           "validite_jours": {"type": "integer", "description": "Durée de validité (défaut 30)."},
           "objet": {"type": "string"}}, ["client", "lignes"]),
    _tool("emettre_facture", "Passe une facture du statut brouillon au statut émise.",
          {"numero": {"type": "string"}}, ["numero"]),
    _tool("enregistrer_paiement",
          "Enregistre un encaissement sur une facture. Sans montant, solde la totalité du reste dû.",
          {"numero": {"type": "string"},
           "montant": {"type": "number", "description": "Montant encaissé. Omettre pour solder la facture."},
           "methode": {"type": "string", "enum": ["transfer", "card", "cash", "check"]},
           "reference": {"type": "string"}}, ["numero"]),
    _tool("ajouter_achat", "Enregistre un reçu ou une facture d'achat (dépense).",
          {"intitule": {"type": "string"}, "montant_ttc": {"type": "number"},
           "tva": {"type": "number", "description": "Montant de TVA (pas le taux)."},
           "fournisseur": {"type": "string"},
           "compte": {"type": "string", "description": "Compte comptable (ex. 6111)."}},
          ["intitule", "montant_ttc"]),
    _tool("envoyer_pdf",
          "Génère le PDF d'une facture ou d'un devis et l'envoie dans la conversation Telegram.",
          {"type": {"type": "string", "enum": ["facture", "devis"]}, "numero": {"type": "string"}},
          ["type", "numero"]),
    _tool("preparer_relance",
          "Prépare le message de relance d'un client pour ses factures impayées (ou pour une facture "
          "précise) et, si le client a un téléphone, le lien WhatsApp pré-rempli. N'envoie rien au client.",
          {"client": {"type": "string"}, "numero": {"type": "string"}}),
]

_ACTIONS = {
    "resume_activite": actions.summary,
    "lister_factures": actions.list_invoices,
    "detail_facture": actions.invoice_detail,
    "impayes_par_client": actions.unpaid_by_client,
    "lister_clients": actions.list_clients,
    "lister_devis": actions.list_quotes,
    "lister_fournisseurs": actions.list_suppliers,
    "lister_achats": actions.list_purchases,
    "tresorerie": actions.treasury,
    "creer_client": actions.create_client,
    "creer_fournisseur": actions.create_supplier,
    "creer_facture": actions.create_invoice,
    "creer_devis": actions.create_quote,
    "emettre_facture": actions.issue_invoice,
    "enregistrer_paiement": actions.record_payment,
    "ajouter_achat": actions.add_purchase,
    "envoyer_pdf": actions.document_pdf,
    "preparer_relance": actions.reminder_message,
}

SYSTEM_PROMPT = """Tu es l'assistant LIBFAD sur Telegram. LIBFAD est le logiciel de facturation et de \
comptabilité de la société de l'utilisateur ; tu lui permets de faire depuis son téléphone ce qu'il fait \
dans l'interface : consulter ses factures, impayés, clients, devis, achats et sa trésorerie, créer des \
clients, fournisseurs, factures, devis et achats, encaisser des paiements, recevoir un PDF, préparer une \
relance.

Les chiffres viennent uniquement des outils. Un montant, un numéro ou une date que tu n'as pas lu dans \
un résultat d'outil n'existe pas : appelle l'outil plutôt que d'estimer, et si l'outil renvoie une \
erreur, transmets-la simplement.

Les actions d'écriture modifient la comptabilité réelle de la société. Exécute-les directement quand la \
demande contient tout ce qu'il faut. S'il manque une information (client, montant, quantité, taux de \
TVA quand il compte), pose la question au lieu de la deviner. Pour un paiement, vérifie que la facture \
et le montant sont sans ambiguïté avant de l'enregistrer : un encaissement saisi par erreur fausse les \
comptes. Une facture est créée en brouillon ; ne l'émets que si l'utilisateur le demande.

Tu écris dans Telegram, lu sur un téléphone : réponds en français (ou dans la langue de l'utilisateur), \
en texte brut sans Markdown ni tableaux, en quelques lignes. Écris les montants avec leur devise. Après \
une action, confirme en une phrase ce qui a été fait, avec le numéro du document créé."""


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


# Conversations en mémoire : {clé: {"messages": [...], "ts": float}}. L'historique
# est en ajout seul ; trop long ou trop ancien, il repart de zéro (jamais tronqué
# au milieu, pour garder intacts les blocs de raisonnement renvoyés au modèle).
_conversations: dict[str, dict] = {}


def reset_conversation(key: str) -> None:
    _conversations.pop(key, None)


def _history(key: str) -> list[dict]:
    conv = _conversations.get(key)
    if not conv:
        return []
    if time.time() - conv["ts"] > CONVERSATION_TTL or len(conv["messages"]) > CONVERSATION_MAX_MESSAGES:
        _conversations.pop(key, None)
        return []
    return list(conv["messages"])


async def _execute(name: str, args: dict, db: AsyncSession, user: User, outbox: list[dict]) -> tuple[str, bool]:
    """Exécute un outil. Renvoie (contenu du tool_result, est_une_erreur)."""
    fn = _ACTIONS.get(name)
    if fn is None:
        return f"Outil inconnu : {name}", True
    if not isinstance(args, dict):
        return "Arguments invalides : un objet JSON est attendu.", True
    accepted = set(inspect.signature(fn).parameters) - {"db", "user"}
    kwargs = {k: v for k, v in args.items() if k in accepted}
    try:
        result = await fn(db, user, **kwargs)
    except actions.ActionError as exc:
        await db.rollback()
        return str(exc), True
    except TypeError as exc:  # argument obligatoire manquant
        await db.rollback()
        return f"Arguments incomplets pour {name} : {exc}", True
    except Exception:
        await db.rollback()
        logger.exception("Échec de l'outil %s", name)
        return "Erreur interne pendant l'exécution de l'action.", True
    if name == "envoyer_pdf":
        outbox.append({"kind": "document", "path": result["path"], "filename": result["filename"]})
        result = {"envoye": True, "fichier": result["filename"], "client": result["client"]}
    return json.dumps(result, ensure_ascii=False, default=str), False


async def run_agent(db: AsyncSession, user: User, tenant: Tenant, conv_key: str,
                    text: str, outbox: list[dict]) -> str:
    """Traite un message utilisateur et renvoie la réponse en texte.

    `outbox` reçoit les pièces à envoyer en plus du texte (PDF). Lève
    `AgentUnavailable` si Claude ne peut pas être joint.
    """
    try:
        client = get_claude()
    except RuntimeError as exc:
        raise AgentUnavailable(str(exc)) from None

    # Un outil en erreur déclenche un rollback, qui expirerait `user` et `tenant`
    # s'ils étaient attachés à la session ; les outils suivants ne pourraient plus les lire.
    for obj in (user, tenant):
        if obj in db:
            db.expunge(obj)

    system = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": (
            f"Société : {tenant.name}. Devise de la société : {tenant.currency}. "
            f"Utilisateur : {user.full_name}. Date du jour : {date.today().strftime('%d/%m/%Y')}.")},
    ]
    messages = _history(conv_key) + [{"role": "user", "content": text}]

    for _ in range(MAX_TURNS):
        try:
            raw = await client.messages.with_raw_response.create(
                model=settings.telegram_agent_model,
                max_tokens=8000,
                system=system,
                tools=TOOLS,
                messages=messages,
                # Si le modèle décline une demande, l'API la rejoue sur le modèle
                # de repli recommandé au lieu de renvoyer un refus sec.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except anthropic.AuthenticationError:
            raise AgentUnavailable("clé Claude invalide") from None
        except anthropic.RateLimitError:
            raise AgentUnavailable("trop de requêtes, réessayez dans un instant") from None
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc.message).lower():
                raise AgentUnavailable("crédit du compte Claude épuisé") from None
            logger.error("Requête Claude refusée : %s", exc.message)
            raise AgentUnavailable("requête refusée par l'API Claude") from None
        except anthropic.APIStatusError as exc:
            logger.error("Erreur API Claude %s : %s", exc.status_code, exc.message)
            raise AgentUnavailable(f"erreur du service Claude ({exc.status_code})") from None
        except anthropic.APIConnectionError:
            raise AgentUnavailable("service Claude injoignable") from None

        # On garde les blocs bruts (dicts) : ils sont renvoyés tels quels au tour
        # suivant, y compris ceux que cette version du SDK ne modélise pas.
        data = json.loads(raw.text)
        content = data.get("content") or []
        stop = data.get("stop_reason")

        if stop == "refusal":
            return "Je ne peux pas traiter cette demande."

        messages.append({"role": "assistant", "content": content})

        if stop == "tool_use":
            results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                body, is_error = await _execute(block.get("name"), block.get("input"), db, user, outbox)
                result = {"type": "tool_result", "tool_use_id": block["id"], "content": body}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})
            continue

        reply = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if stop == "max_tokens":
            reply += "\n(Réponse tronquée.)"
        _conversations[conv_key] = {"messages": messages, "ts": time.time()}
        return reply or "C'est fait."

    return "Cette demande nécessite trop d'étapes. Reformulez-la en plusieurs messages."
