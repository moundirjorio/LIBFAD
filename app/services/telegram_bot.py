"""Bot Telegram de LIBFAD : l'interface dans une conversation.

Chaque société branche son propre bot (jeton @BotFather saisi dans
Automatisation → Bot Telegram). Le serveur interroge Telegram en « long
polling » (aucune URL publique nécessaire) et traite trois types d'entrées :

  * des commandes (/impayes, /tresorerie, /facture 00008…) ;
  * des assistants pas-à-pas pour créer (/nouvellefacture, /paiement…) ;
  * du texte libre, confié à l'agent Claude quand il est disponible, sinon
    rapproché d'une commande par mots-clés.

Une conversation n'accède aux données qu'après avoir été liée à un utilisateur
LIBFAD par un code à usage unique généré dans l'interface.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.deps import _apply_active_dossier
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.supplier import Supplier
from app.models.telegram import TelegramLink
from app.models.tenant import Tenant
from app.models.user import User
from app.services import assistant_actions as actions
from app.services import assistant_agent as agent
from app.services.assistant_actions import ActionError

logger = logging.getLogger("libfad.telegram")
# httpx journalise l'URL complète de chaque requête au niveau INFO ; or l'URL
# de l'API Telegram contient le jeton du bot. On le garde hors des journaux.
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
LONG_POLL_SECONDS = 25
WIZARD_TTL = 15 * 60
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # limite de téléchargement de l'API Bot

BOT_COMMANDS = [
    ("menu", "Menu principal"),
    ("resume", "Chiffres clés de la société"),
    ("impayes", "Factures impayées par client"),
    ("factures", "Dernières factures"),
    ("facture", "Détail d'une facture : /facture 00008"),
    ("pdf", "Recevoir un PDF : /pdf 00008"),
    ("devis", "Derniers devis"),
    ("clients", "Clients (recherche : /clients nom)"),
    ("fournisseurs", "Fournisseurs"),
    ("achats", "Derniers achats"),
    ("tresorerie", "Soldes et derniers mouvements"),
    ("relance", "Préparer une relance : /relance client"),
    ("nouvellefacture", "Créer une facture"),
    ("nouveaudevis", "Créer un devis"),
    ("nouveauclient", "Créer un client"),
    ("nouveaufournisseur", "Créer un fournisseur"),
    ("paiement", "Encaisser un paiement"),
    ("achat", "Enregistrer un achat"),
    ("emettre", "Émettre une facture brouillon"),
    ("annuler", "Annuler la saisie en cours"),
    ("aide", "Aide"),
]

HELP = (
    "Voici ce que je sais faire.\n\n"
    "📊 Consulter\n"
    "/resume — chiffres clés\n"
    "/impayes — impayés par client\n"
    "/factures — dernières factures\n"
    "/facture 00008 — détail d'une facture\n"
    "/pdf 00008 — recevoir le PDF (/pdf devis 00003)\n"
    "/devis · /clients · /fournisseurs · /achats\n"
    "/tresorerie — soldes et mouvements\n\n"
    "✍️ Créer\n"
    "/nouvellefacture · /nouveaudevis\n"
    "/nouveauclient · /nouveaufournisseur\n"
    "/paiement — encaisser une facture\n"
    "/achat — enregistrer une dépense\n"
    "/emettre 00008 — émettre un brouillon\n\n"
    "🔔 Relancer\n"
    "/relance Nom du client — message + lien WhatsApp\n\n"
    "📎 Envoyez un relevé bancaire (PDF ou CSV) pour l'importer.\n"
    "💬 Vous pouvez aussi écrire normalement : « combien me doit Bouygues ? »\n\n"
    "/annuler interrompt une saisie · /deconnexion délie ce compte."
)


class TelegramError(Exception):
    def __init__(self, description: str, code: int | None = None):
        super().__init__(description)
        self.description = description
        self.code = code


# --- Client de l'API Bot ------------------------------------------------------

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    return _http


def _decode(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        raise TelegramError(f"Réponse inattendue de Telegram (HTTP {resp.status_code})", resp.status_code) from None


async def tg_call(token: str, method: str, *, http_timeout: float | None = None, **params):
    """Appelle une méthode de l'API Bot et renvoie son `result`."""
    url = f"{settings.telegram_api_base}/bot{token}/{method}"
    body = {k: v for k, v in params.items() if v is not None}
    extra = {"timeout": http_timeout} if http_timeout is not None else {}
    try:
        resp = await _client().post(url, json=body, **extra)
    except httpx.HTTPError as exc:
        raise TelegramError(f"Telegram injoignable ({type(exc).__name__})") from None
    data = _decode(resp)
    if not data.get("ok"):
        raise TelegramError(data.get("description") or "Erreur Telegram", data.get("error_code") or resp.status_code)
    return data.get("result")


async def tg_send_document(token: str, chat_id: int, path: str, filename: str, caption: str | None = None):
    url = f"{settings.telegram_api_base}/bot{token}/sendDocument"
    content = await asyncio.to_thread(Path(path).read_bytes)
    form = {"chat_id": str(chat_id)}
    if caption:
        form["caption"] = caption[:1000]
    try:
        resp = await _client().post(
            url, data=form, files={"document": (filename, content, "application/pdf")}, timeout=60.0)
    except httpx.HTTPError as exc:
        raise TelegramError(f"Telegram injoignable ({type(exc).__name__})") from None
    data = _decode(resp)
    if not data.get("ok"):
        raise TelegramError(data.get("description") or "Erreur Telegram", data.get("error_code"))
    return data.get("result")


async def tg_download(token: str, file_id: str) -> bytes:
    info = await tg_call(token, "getFile", file_id=file_id)
    url = f"{settings.telegram_api_base}/file/bot{token}/{info['file_path']}"
    try:
        resp = await _client().get(url, timeout=60.0)
    except httpx.HTTPError as exc:
        raise TelegramError(f"Téléchargement impossible ({type(exc).__name__})") from None
    if resp.status_code != 200:
        raise TelegramError(f"Téléchargement impossible (HTTP {resp.status_code})", resp.status_code)
    return resp.content


def _chunks(text: str, size: int = 3900) -> list[str]:
    """Découpe un message long (limite Telegram : 4096 caractères) aux sauts de ligne."""
    out = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    out.append(text)
    return out


async def send_text(token: str, chat_id: int, text: str, keyboard: list[list[dict]] | None = None):
    parts = _chunks(text or "…")
    for i, part in enumerate(parts):
        markup = {"inline_keyboard": keyboard} if keyboard and i == len(parts) - 1 else None
        await tg_call(token, "sendMessage", chat_id=chat_id, text=part,
                      reply_markup=markup, disable_web_page_preview=True)


def btn(label: str, data: str) -> dict:
    # callback_data est limité à 64 octets : on coupe sans casser un caractère accentué
    data = data.encode("utf-8")[:64].decode("utf-8", errors="ignore")
    return {"text": label, "callback_data": data}


def url_btn(label: str, url: str) -> dict:
    return {"text": label, "url": url}


# --- Mise en forme ------------------------------------------------------------

def money(value, currency: str | None = None) -> str:
    s = f"{float(value or 0):,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} {currency}" if currency else s


def _totals(by_currency: dict[str, float]) -> str:
    return " + ".join(money(v, c) for c, v in by_currency.items()) or "0,00"


def _d(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def fmt_summary(s: dict) -> str:
    cur = s["devise_societe"]
    return (
        f"📊 {s['societe']}\n\n"
        f"Facturé : {_totals(s['facture'])}\n"
        f"Encaissé : {_totals(s['encaisse'])}\n"
        f"À encaisser : {_totals(s['a_encaisser'])} ({s['nb_impayees']} facture(s))\n"
        f"Dont en retard : {_totals(s['en_retard'])} ({s['nb_en_retard']})\n\n"
        f"🏦 Trésorerie : {money(s['tresorerie'], cur)}\n"
        f"🧾 Achats du mois : {money(s['achats_du_mois'], cur)}\n"
        f"👥 Clients : {s['clients']} · Devis en cours : {s['devis_en_cours']}"
    )


def _invoice_line(f: dict) -> str:
    if f["reste_du"] > 0:
        tail = f"{money(f['reste_du'], f['devise'])} dû · éch. {_d(f['echeance'])}"
        tail += " ⚠️ en retard" if f["en_retard"] else ""
    else:
        tail = f"{money(f['total_ttc'], f['devise'])} · {f['statut']}"
    return f"• {f['numero']} — {f['client']}\n   {tail}"


def fmt_invoices(data: dict, title: str = "🧾 Factures") -> str:
    rows = data["factures"]
    if not rows:
        return f"{title}\n\nAucune facture."
    more = f"\n\n… {data['total'] - len(rows)} autre(s)." if data["total"] > len(rows) else ""
    return f"{title} ({data['total']})\n\n" + "\n".join(_invoice_line(f) for f in rows) + more


def fmt_invoice_detail(f: dict) -> str:
    lines = "\n".join(
        f"• {ln['description']} — {ln['quantite']:g} × {money(ln['prix_unitaire_ht'])} (TVA {ln['tva_pct']:g} %)"
        for ln in f["lignes"]) or "(aucune ligne)"
    late = " ⚠️ en retard" if f["en_retard"] else ""
    return (
        f"🧾 Facture {f['numero']} — {f['statut']}{late}\n"
        f"Client : {f['client']}\n"
        f"Émise le {_d(f['date'])} · échéance {_d(f['echeance'])}\n\n"
        f"{lines}\n\n"
        f"Total HT : {money(f['total_ht'], f['devise'])}\n"
        f"TVA : {money(f['tva'], f['devise'])}\n"
        f"Total TTC : {money(f['total_ttc'], f['devise'])}\n"
        f"Déjà payé : {money(f['deja_paye'], f['devise'])}\n"
        f"Reste dû : {money(f['reste_du'], f['devise'])}"
    )


def fmt_unpaid(data: dict) -> str:
    groups = data["clients"]
    if not groups:
        return "✅ Aucune facture impayée."
    out = ["💰 Impayés par client"]
    for g in groups:
        out.append(f"\n{g['client']} — {money(g['total_du'], g['devise'])}")
        for f in g["factures"]:
            late = " ⚠️" if f["en_retard"] else ""
            out.append(f"   • {f['numero']} : {money(f['reste_du'], f['devise'])} · éch. {_d(f['echeance'])}{late}")
    totals: dict[str, float] = {}
    for g in groups:
        totals[g["devise"]] = totals.get(g["devise"], 0.0) + g["total_du"]
    out.append(f"\nTotal à encaisser : {_totals(totals)}")
    return "\n".join(out)


def fmt_clients(data: dict) -> str:
    rows = data["clients"]
    if not rows:
        return "👥 Aucun client trouvé."
    lines = []
    for c in rows:
        extra = " · ".join(x for x in (c["telephone"], c["email"], c["ville"]) if x)
        lines.append(f"• {c['nom']}" + (f"\n   {extra}" if extra else ""))
    more = f"\n\n… {data['total'] - len(rows)} autre(s). Affinez : /clients nom" if data["total"] > len(rows) else ""
    return f"👥 Clients ({data['total']})\n\n" + "\n".join(lines) + more


def fmt_quotes(data: dict) -> str:
    rows = data["devis"]
    if not rows:
        return "📄 Aucun devis."
    return "📄 Devis\n\n" + "\n".join(
        f"• {q['numero']} — {q['client']}\n   {money(q['total_ttc'], q['devise'])} · {q['statut']} · "
        f"valable jusqu'au {_d(q['valable_jusqu_au'])}" for q in rows)


def fmt_suppliers(data: dict) -> str:
    rows = data["fournisseurs"]
    if not rows:
        return "🚚 Aucun fournisseur."
    return "🚚 Fournisseurs\n\n" + "\n".join(
        f"• {s['nom']}" + (f" (compte {s['compte']})" if s["compte"] else "") for s in rows)


def fmt_purchases(data: dict, currency: str) -> str:
    rows = data["achats"]
    head = f"🧾 Achats — {money(data['total_du_mois'], currency)} ce mois-ci"
    if not rows:
        return head + "\n\nAucun achat enregistré."
    return head + "\n\n" + "\n".join(
        f"• {_d(a['date'])} — {a['intitule']}\n   {money(a['montant_ttc'], currency)} TTC · {a['statut']}"
        for a in rows)


def fmt_treasury(data: dict) -> str:
    accounts = data["comptes"]
    if not accounts:
        return "🏦 Aucun compte bancaire. Ajoutez-en un dans l'interface (Trésorerie)."
    out = ["🏦 Trésorerie", ""]
    out += [f"• {a['nom']}" + (f" ({a['banque']})" if a["banque"] else "") + f" : {money(a['solde'], a['devise'])}"
            for a in accounts]
    if data["derniers_mouvements"]:
        out += ["", "Derniers mouvements :"]
        out += [f"   {_d(m['date'])}  {'+' if m['montant'] > 0 else ''}{money(m['montant'])}  {m['libelle'][:34]}"
                for m in data["derniers_mouvements"]]
    if data["encaissements_a_rapprocher"]:
        out += ["", f"🔗 {data['encaissements_a_rapprocher']} encaissement(s) à rapprocher (voir l'interface)."]
    return "\n".join(out)


# --- Contexte d'une conversation ----------------------------------------------

@dataclass
class Ctx:
    token: str
    chat_id: int
    db: AsyncSession
    user: User
    tenant: Tenant
    outbox: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple[int, int]:
        return (self.tenant.id, self.chat_id)

    async def send(self, text: str, keyboard: list[list[dict]] | None = None) -> None:
        await send_text(self.token, self.chat_id, text, keyboard)

    async def flush(self) -> None:
        """Envoie les pièces préparées par une action (PDF)."""
        for item in self.outbox:
            await tg_send_document(self.token, self.chat_id, item["path"], item["filename"])
        self.outbox.clear()


# --- Assistants pas-à-pas -----------------------------------------------------

_wizards: dict[tuple[int, int], dict] = {}
_pending_docs: dict[tuple[int, int], dict] = {}
_ai_notice_sent: set[tuple[int, int]] = set()

CANCEL_ROW = [btn("✖ Annuler", "x")]
METHODS = {"transfer": "virement", "check": "chèque", "cash": "espèces", "card": "carte"}


def _step(key, prompt, kind="text", optional=False, choices=None, positive=False, maximum=None) -> dict:
    return {"key": key, "prompt": prompt, "kind": kind, "optional": optional, "choices": choices,
            "positive": positive, "maximum": maximum}


def _line_recap(d: dict) -> str:
    return (f"Ligne : {d['description']} — {float(d['quantite']):g} × {money(d['prix'])} "
            f"(TVA {float(d['tva']):g} %).\nAjouter une autre ligne ?")


_LINE_STEPS = [
    _step("description", "📝 Description de la ligne ?"),
    _step("quantite", "Quantité ?", "amount", choices=[("1", "1")], positive=True),
    _step("prix", "Prix unitaire HT ?", "amount"),
    _step("tva", "Taux de TVA en % ?", "amount", maximum=100,
          choices=[("0 %", "0"), ("10 %", "10"), ("14 %", "14"), ("20 %", "20")]),
    _step("encore", _line_recap, "choice", choices=[("➕ Autre ligne", "oui"), ("✅ Terminer", "non")]),
]


def _payment_recap(d: dict) -> str:
    amount = money(d["montant"], d["_devise"]) if d.get("montant") is not None else \
        f"la totalité du reste dû ({money(d['_reste'], d['_devise'])})"
    return (f"Encaisser {amount} sur la facture {d['facture']} ({d['_client']}), "
            f"par {METHODS[d['methode']]} ?")


async def _fin_client(ctx: Ctx, d: dict) -> None:
    r = await actions.create_client(ctx.db, ctx.user, d["nom"], d.get("email"), d.get("telephone"),
                                    ville=d.get("ville"))
    await ctx.send(f"✅ Client « {r['nom']} » créé.",
                   [[btn("🧾 Lui créer une facture", "c:nouvellefacture")]])


async def _fin_supplier(ctx: Ctx, d: dict) -> None:
    r = await actions.create_supplier(ctx.db, ctx.user, d["nom"], d.get("email"), d.get("telephone"))
    await ctx.send(f"✅ Fournisseur « {r['nom']} » créé.")


async def _fin_invoice(ctx: Ctx, d: dict) -> None:
    r = await actions.create_invoice(ctx.db, ctx.user, d["client"], d["lignes"],
                                     emettre=d.get("emettre") == "oui")
    await ctx.send(
        f"✅ Facture {r['numero']} créée ({r['statut'].lower()}) pour {r['client']} : "
        f"{money(r['total_ttc'], r['devise'])} TTC, échéance {_d(r['echeance'])}.",
        [[btn("📄 Recevoir le PDF", f"c:pdf {r['numero']}")]])


async def _fin_quote(ctx: Ctx, d: dict) -> None:
    r = await actions.create_quote(ctx.db, ctx.user, d["client"], d["lignes"])
    await ctx.send(
        f"✅ Devis {r['numero']} créé pour {r['client']} : {money(r['total_ttc'], r['devise'])} TTC, "
        f"valable jusqu'au {_d(r['valable_jusqu_au'])}.",
        [[btn("📄 Recevoir le PDF", f"c:pdf devis {r['numero']}")]])


async def _fin_payment(ctx: Ctx, d: dict) -> None:
    if d.get("confirmer") != "oui":
        await ctx.send("Paiement annulé, rien n'a été enregistré.")
        return
    r = await actions.record_payment(ctx.db, ctx.user, d["facture"], d.get("montant"), d["methode"])
    rest = "facture soldée ✅" if r["reste_du"] <= 0 else f"reste dû {money(r['reste_du'], r['devise'])}"
    await ctx.send(f"💰 {money(r['paiement_enregistre'], r['devise'])} encaissés sur {r['numero']} — {rest}.")


async def _fin_purchase(ctx: Ctx, d: dict) -> None:
    r = await actions.add_purchase(ctx.db, ctx.user, d["intitule"], d["montant_ttc"], d.get("tva") or 0,
                                   d.get("fournisseur"))
    sup = f" · fournisseur {r['fournisseur']}" if r["fournisseur"] else ""
    await ctx.send(f"✅ Achat « {r['intitule']} » enregistré : "
                   f"{money(r['montant_ttc'], ctx.tenant.currency)} TTC{sup}. À vérifier dans l'interface.")


WIZARDS: dict[str, dict] = {
    "client": {"title": "Nouveau client", "finish": _fin_client, "steps": [
        _step("nom", "👤 Nom du client ?"),
        _step("email", "Email ?", optional=True),
        _step("telephone", "Téléphone ? (sert aux relances WhatsApp)", optional=True),
        _step("ville", "Ville ?", optional=True),
    ]},
    "fournisseur": {"title": "Nouveau fournisseur", "finish": _fin_supplier, "steps": [
        _step("nom", "🚚 Nom du fournisseur ?"),
        _step("email", "Email ?", optional=True),
        _step("telephone", "Téléphone ?", optional=True),
    ]},
    "facture": {"title": "Nouvelle facture", "finish": _fin_invoice, "steps": [
        _step("client", "👤 Pour quel client ? Choisissez ou tapez son nom.", "client"),
        *_LINE_STEPS,
        _step("emettre", "Émettre la facture maintenant, ou la garder en brouillon ?", "choice",
              choices=[("📤 Émettre", "oui"), ("📝 Brouillon", "non")]),
    ]},
    "devis": {"title": "Nouveau devis", "finish": _fin_quote, "steps": [
        _step("client", "👤 Pour quel client ? Choisissez ou tapez son nom.", "client"),
        *_LINE_STEPS,
    ]},
    "paiement": {"title": "Encaisser un paiement", "finish": _fin_payment, "steps": [
        _step("facture", "🧾 Quelle facture ? Choisissez ou tapez son numéro.", "invoice"),
        _step("montant", "Montant encaissé ?", "amount", optional=True, positive=True),
        _step("methode", "Mode de paiement ?", "choice",
              choices=[("Virement", "transfer"), ("Chèque", "check"), ("Espèces", "cash"), ("Carte", "card")]),
        _step("confirmer", _payment_recap, "choice", choices=[("✅ Confirmer", "oui"), ("✖ Annuler", "non")]),
    ]},
    "achat": {"title": "Nouvel achat", "finish": _fin_purchase, "steps": [
        _step("intitule", "🧾 Intitulé de l'achat ? (ex. Orange abonnement)"),
        _step("montant_ttc", "Montant TTC ?", "amount", positive=True),
        _step("tva", "Montant de TVA inclus ?", "amount", optional=True),
        _step("fournisseur", "Fournisseur ? Choisissez ou tapez son nom.", "supplier", optional=True),
    ]},
}


def _pairs(buttons: list[dict]) -> list[list[dict]]:
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


async def _step_keyboard(ctx: Ctx, step: dict) -> list[list[dict]]:
    tid = ctx.tenant.id
    rows: list[list[dict]] = []
    if step["choices"]:
        rows += _pairs([btn(label, f"w:{value}") for label, value in step["choices"]])
    if step["kind"] == "client":
        clients = await ctx.db.execute(
            select(Client.id, Client.name).where(Client.tenant_id == tid).order_by(Client.name).limit(8))
        rows += _pairs([btn(name[:28], f"w:{cid}") for cid, name in clients.all()])
    elif step["kind"] == "invoice":
        invs = await ctx.db.scalars(
            select(Invoice).where(Invoice.tenant_id == tid, Invoice.status.in_(actions.UNPAID_STATUSES),
                                  Invoice.amount_due > 0).order_by(Invoice.due_date).limit(8))
        rows += [[btn(f"{i.number} · {money(i.amount_due, i.currency)}", f"w:{i.number}")] for i in invs]
    elif step["kind"] == "supplier":
        sups = await ctx.db.execute(
            select(Supplier.name).where(Supplier.tenant_id == tid).order_by(Supplier.name).limit(8))
        rows += _pairs([btn(name[:28], f"w:{name[:28]}") for (name,) in sups.all()])
    last = list(CANCEL_ROW)
    if step["optional"]:
        last.insert(0, btn("⏭ Passer", "w:-"))
    rows.append(last)
    return rows


async def _ask(ctx: Ctx) -> None:
    state = _wizards[ctx.key]
    step = WIZARDS[state["name"]]["steps"][state["i"]]
    prompt = step["prompt"](state["data"]) if callable(step["prompt"]) else step["prompt"]
    if step["key"] == "montant" and state["name"] == "paiement":
        d = state["data"]
        prompt += f"\nReste dû : {money(d['_reste'], d['_devise'])}. « Passer » pour tout solder."
    await ctx.send(prompt, await _step_keyboard(ctx, step))


async def start_wizard(ctx: Ctx, name: str, preset: dict | None = None) -> None:
    _wizards[ctx.key] = {"name": name, "i": 0, "data": {}, "ts": time.time()}
    await ctx.send(f"— {WIZARDS[name]['title']} —  (/annuler pour arrêter)")
    if preset:  # valeur déjà fournie avec la commande (ex. /paiement 00008)
        key, value = next(iter(preset.items()))
        if WIZARDS[name]["steps"][0]["key"] == key:
            await wizard_input(ctx, value)
            return
    await _ask(ctx)


async def _parse_step(ctx: Ctx, step: dict, raw: str, data: dict):
    raw = (raw or "").strip()
    if step["optional"] and raw.lower() in ("-", "passer", "aucun", "non"):
        return None
    kind = step["kind"]
    if kind == "amount":
        value = actions.to_decimal(raw.replace("%", ""), step["key"])
        if value < 0 or (step["positive"] and value == 0):
            raise ActionError("Entrez un nombre positif.")
        if step["maximum"] is not None and value > step["maximum"]:
            raise ActionError(f"La valeur maximale est {step['maximum']}.")
        return value
    if kind == "choice":
        for label, value in step["choices"]:
            if raw.lower() in (value.lower(), label.lower()):
                return value
        raise ActionError("Choisissez l'une des options proposées.")
    if kind == "client":
        client = await actions.find_client(ctx.db, ctx.tenant.id, raw)
        return str(client.id)
    if kind == "invoice":
        inv = await actions.find_invoice(ctx.db, ctx.tenant.id, raw)
        if inv.status == "cancelled" or float(inv.amount_due or 0) <= 0:
            raise ActionError(f"La facture {inv.number} n'a pas de reste dû.")
        client = await ctx.db.scalar(select(Client).where(Client.id == inv.client_id))
        data.update({"_reste": float(inv.amount_due), "_devise": inv.currency,
                     "_client": client.name if client else "—"})
        return inv.number
    if not raw:
        raise ActionError("Cette information est obligatoire.")
    return raw[:200]


async def wizard_input(ctx: Ctx, raw: str) -> None:
    state = _wizards.get(ctx.key)
    if not state:
        await ctx.send("Cette saisie a expiré. Relancez la commande.")
        return
    spec = WIZARDS[state["name"]]
    steps, data = spec["steps"], state["data"]
    step = steps[state["i"]]
    try:
        value = await _parse_step(ctx, step, raw, data)
    except ActionError as exc:
        await ctx.send(f"⚠️ {exc}")
        await _ask(ctx)
        return
    data[step["key"]] = value
    state["ts"] = time.time()

    if step["key"] == "encore":  # clôt la ligne en cours ; « oui » en ouvre une autre
        data.setdefault("lignes", []).append({
            "description": data.pop("description"), "quantite": data.pop("quantite"),
            "prix_unitaire_ht": data.pop("prix"), "tva_pct": data.pop("tva")})
        if value == "oui":
            state["i"] = next(i for i, s in enumerate(steps) if s["key"] == "description")
            await _ask(ctx)
            return

    state["i"] += 1
    if state["i"] < len(steps):
        await _ask(ctx)
        return
    _wizards.pop(ctx.key, None)
    try:
        await spec["finish"](ctx, data)
    except ActionError as exc:
        await ctx.db.rollback()
        await ctx.send(f"⚠️ {exc}")


# --- Commandes ----------------------------------------------------------------

MENU = [
    [btn("📊 Résumé", "c:resume"), btn("💰 Impayés", "c:impayes")],
    [btn("🧾 Factures", "c:factures"), btn("📄 Devis", "c:devis")],
    [btn("🏦 Trésorerie", "c:tresorerie"), btn("🛒 Achats", "c:achats")],
    [btn("➕ Facture", "c:nouvellefacture"), btn("➕ Devis", "c:nouveaudevis")],
    [btn("➕ Client", "c:nouveauclient"), btn("💵 Encaisser", "c:paiement")],
    [btn("❓ Aide", "c:aide")],
]


async def cmd_menu(ctx: Ctx, arg: str) -> None:
    await ctx.send(f"{ctx.tenant.name} — que voulez-vous faire ?", MENU)


async def cmd_help(ctx: Ctx, arg: str) -> None:
    await ctx.send(HELP)


async def cmd_summary(ctx: Ctx, arg: str) -> None:
    await ctx.send(fmt_summary(await actions.summary(ctx.db, ctx.user)))


async def cmd_invoices(ctx: Ctx, arg: str) -> None:
    data = await actions.list_invoices(ctx.db, ctx.user, client=arg or None)
    await ctx.send(fmt_invoices(data) + "\n\nDétail : /facture numéro")


async def cmd_unpaid(ctx: Ctx, arg: str) -> None:
    data = await actions.unpaid_by_client(ctx.db, ctx.user)
    keyboard = [[btn(f"🔔 Relancer {g['client'][:22]}", f"c:relance {g['client'][:40]}")]
                for g in data["clients"][:5]]
    await ctx.send(fmt_unpaid(data), keyboard or None)


async def cmd_invoice(ctx: Ctx, arg: str) -> None:
    if not arg:
        await ctx.send("Indiquez le numéro : /facture 00008")
        return
    f = await actions.invoice_detail(ctx.db, ctx.user, arg)
    row = [btn("📄 PDF", f"c:pdf {f['numero']}")]
    if f["reste_du"] > 0 and f["statut"] != "Brouillon":
        row += [btn("💵 Encaisser", f"c:paiement {f['numero']}"), btn("🔔 Relancer", f"c:relance {f['numero']}")]
    elif f["statut"] == "Brouillon":
        row.append(btn("📤 Émettre", f"c:emettre {f['numero']}"))
    await ctx.send(fmt_invoice_detail(f), [row])


async def cmd_pdf(ctx: Ctx, arg: str) -> None:
    parts = arg.split()
    if not parts:
        await ctx.send("Indiquez le numéro : /pdf 00008 (ou /pdf devis 00003)")
        return
    kind = "devis" if parts[0].lower() == "devis" and len(parts) > 1 else "facture"
    numero = parts[1] if kind == "devis" else parts[0]
    r = await actions.document_pdf(ctx.db, ctx.user, kind, numero)
    await tg_call(ctx.token, "sendChatAction", chat_id=ctx.chat_id, action="upload_document")
    await tg_send_document(ctx.token, ctx.chat_id, r["path"], r["filename"],
                           caption=f"{kind.capitalize()} {r['numero']} — {r['client']}")


async def cmd_quotes(ctx: Ctx, arg: str) -> None:
    await ctx.send(fmt_quotes(await actions.list_quotes(ctx.db, ctx.user)))


async def cmd_clients(ctx: Ctx, arg: str) -> None:
    await ctx.send(fmt_clients(await actions.list_clients(ctx.db, ctx.user, arg or None)))


async def cmd_suppliers(ctx: Ctx, arg: str) -> None:
    await ctx.send(fmt_suppliers(await actions.list_suppliers(ctx.db, ctx.user, arg or None)))


async def cmd_purchases(ctx: Ctx, arg: str) -> None:
    await ctx.send(fmt_purchases(await actions.list_purchases(ctx.db, ctx.user), ctx.tenant.currency))


async def cmd_treasury(ctx: Ctx, arg: str) -> None:
    await ctx.send(fmt_treasury(await actions.treasury(ctx.db, ctx.user)))


async def cmd_reminder(ctx: Ctx, arg: str) -> None:
    if not arg:
        await ctx.send("Indiquez le client ou la facture : /relance Bouygues  ou  /relance 00008")
        return
    r = None
    if any(ch.isdigit() for ch in arg):  # ressemble à un numéro de facture
        try:
            r = await actions.reminder_message(ctx.db, ctx.user, numero=arg)
        except ActionError:
            r = None
    if r is None:
        r = await actions.reminder_message(ctx.db, ctx.user, client=arg)
    if r["whatsapp_url"]:
        keyboard = [[url_btn("💬 Ouvrir dans WhatsApp", r["whatsapp_url"])]]
        note = "Le bouton ouvre WhatsApp avec ce message pré-rempli ; rien n'est envoyé sans vous."
    else:
        keyboard = None
        note = "Ce client n'a pas de téléphone : copiez le message, ou ajoutez son numéro dans sa fiche."
    await ctx.send(f"🔔 Relance — {r['client']} ({r['total']})\n\n{r['message']}\n\n{note}", keyboard)


async def cmd_issue(ctx: Ctx, arg: str) -> None:
    if not arg:
        await ctx.send("Indiquez le numéro : /emettre 00008")
        return
    r = await actions.issue_invoice(ctx.db, ctx.user, arg)
    await ctx.send(f"📤 Facture {r['numero']} émise : {money(r['total_ttc'], r['devise'])} "
                   f"à régler avant le {_d(r['echeance'])}.")


async def cmd_cancel(ctx: Ctx, arg: str) -> None:
    had = _wizards.pop(ctx.key, None) or _pending_docs.pop(ctx.key, None)
    await ctx.send("Saisie annulée." if had else "Aucune saisie en cours.")


async def cmd_reset(ctx: Ctx, arg: str) -> None:
    agent.reset_conversation(str(ctx.key))
    await ctx.send("Nouvelle conversation : j'ai oublié les échanges précédents.")


async def cmd_unlink(ctx: Ctx, arg: str) -> None:
    await ctx.db.execute(delete(TelegramLink).where(
        TelegramLink.tenant_id == ctx.tenant.id, TelegramLink.chat_id == ctx.chat_id))
    await ctx.db.commit()
    agent.reset_conversation(str(ctx.key))
    _wizards.pop(ctx.key, None)
    await ctx.send("Compte délié. Pour revenir, générez un nouveau code dans LIBFAD → Bot Telegram.")


def _wizard_cmd(name: str, preset_key: str | None = None):
    async def run(ctx: Ctx, arg: str) -> None:
        await start_wizard(ctx, name, {preset_key: arg} if preset_key and arg else None)
    return run


COMMANDS = {
    "start": cmd_menu, "menu": cmd_menu, "aide": cmd_help, "help": cmd_help,
    "resume": cmd_summary, "factures": cmd_invoices, "impayes": cmd_unpaid,
    "facture": cmd_invoice, "pdf": cmd_pdf, "devis": cmd_quotes, "clients": cmd_clients,
    "fournisseurs": cmd_suppliers, "achats": cmd_purchases, "tresorerie": cmd_treasury,
    "relance": cmd_reminder, "emettre": cmd_issue, "annuler": cmd_cancel,
    "nouveau": cmd_reset, "deconnexion": cmd_unlink,
    "nouveauclient": _wizard_cmd("client"), "nouveaufournisseur": _wizard_cmd("fournisseur"),
    "nouvellefacture": _wizard_cmd("facture"), "nouveaudevis": _wizard_cmd("devis"),
    "paiement": _wizard_cmd("paiement", "facture"), "achat": _wizard_cmd("achat"),
}


async def run_command(ctx: Ctx, line: str) -> None:
    name, _, arg = line.strip().lstrip("/").partition(" ")
    name = name.split("@")[0].lower()
    handler = COMMANDS.get(name)
    if handler is None:
        await ctx.send(f"Commande inconnue : /{name}. Tapez /aide.")
        return
    if name != "annuler":
        _wizards.pop(ctx.key, None)  # une commande interrompt l'assistant en cours
    try:
        await handler(ctx, arg.strip())
    except ActionError as exc:
        await ctx.db.rollback()
        await ctx.send(f"⚠️ {exc}")


# --- Texte libre --------------------------------------------------------------

# Sans IA, on rapproche le message d'une commande par mots-clés (ordre = priorité).
_KEYWORDS = [
    (("impay", "doit", "doivent", "reste a", "retard", "encaisser", "creance"), "impayes"),
    (("tresor", "solde", "banque", "compte bancaire"), "tresorerie"),
    (("resume", "bilan", "chiffre", "tableau de bord", "ou j en suis", "situation"), "resume"),
    (("fournisseur",), "fournisseurs"),
    (("achat", "depense"), "achats"),
    (("devis",), "devis"),
    (("client",), "clients"),
    (("facture",), "factures"),
]


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").replace("'", " ")


def keyword_command(text: str) -> str | None:
    plain = _plain(text)
    for words, command in _KEYWORDS:
        if any(w in plain for w in words):
            return command
    return None


async def handle_free_text(ctx: Ctx, text: str) -> None:
    reason = "aucune clé Claude n'est configurée"
    if agent.is_configured():
        try:
            await tg_call(ctx.token, "sendChatAction", chat_id=ctx.chat_id, action="typing")
        except TelegramError:
            pass
        try:
            reply = await agent.run_agent(ctx.db, ctx.user, ctx.tenant, str(ctx.key), text, ctx.outbox)
        except agent.AgentUnavailable as exc:
            reason = str(exc)
            ctx.outbox.clear()
        else:
            await ctx.send(reply)
            await ctx.flush()
            return
    command = keyword_command(text)
    if ctx.key not in _ai_notice_sent:
        _ai_notice_sent.add(ctx.key)
        await ctx.send(f"ℹ️ L'assistant IA est indisponible ({reason}). "
                       "Je réponds avec les commandes — tapez /aide pour la liste.")
    if command:
        await run_command(ctx, command)
    else:
        await ctx.send("Je n'ai pas compris. Voici ce que je peux faire :", MENU)


# --- Import de relevé envoyé en pièce jointe ----------------------------------

async def handle_document(ctx: Ctx, doc: dict) -> None:
    name = doc.get("file_name") or "fichier"
    if not name.lower().endswith((".pdf", ".csv")):
        await ctx.send("Je n'importe que les relevés bancaires en PDF ou CSV.")
        return
    if (doc.get("file_size") or 0) > MAX_DOCUMENT_BYTES:
        await ctx.send("Fichier trop volumineux (20 Mo maximum).")
        return
    _pending_docs[ctx.key] = {"file_id": doc["file_id"], "name": name, "ts": time.time()}
    await ctx.send(
        f"📎 « {name} » reçu. L'importer comme relevé bancaire ? Les mouvements seront ajoutés "
        "au compte bancaire par défaut.",
        [[btn("✅ Importer le relevé", "imp:ok"), btn("✖ Annuler", "imp:no")]])


async def confirm_document(ctx: Ctx, accept: bool) -> None:
    pending = _pending_docs.pop(ctx.key, None)
    if not pending or time.time() - pending["ts"] > WIZARD_TTL:
        await ctx.send("Ce fichier a expiré, renvoyez-le.")
        return
    if not accept:
        await ctx.send("Import annulé.")
        return
    try:
        data = await tg_download(ctx.token, pending["file_id"])
        r = await actions.import_statement(ctx.db, ctx.user, data, pending["name"])
    except ActionError as exc:
        await ctx.db.rollback()
        await ctx.send(f"⚠️ {exc}")
        return
    if not r["importes"]:
        await ctx.send("Aucun mouvement reconnu dans ce fichier. Vérifiez qu'il s'agit d'un relevé "
                       "avec des colonnes Date / Libellé / Débit / Crédit (PDF texte, pas un scan).")
        return
    await ctx.send(f"✅ {r['importes']} mouvement(s) importé(s) dans « {r['compte']} ». "
                   f"Nouveau solde : {money(r['solde'], r['devise'])}.\n"
                   "Le rapprochement avec les factures se fait dans l'interface (Trésorerie → Rapprochement).")


# --- Liaison d'une conversation à un utilisateur ------------------------------

NOT_LINKED = ("🔒 Ce bot est privé.\n\nPour le relier à votre compte : ouvrez LIBFAD → Bot Telegram → "
              "« Générer un code », puis utilisez le lien affiché (ou envoyez-moi /start suivi du code).")


async def _link_chat(db: AsyncSession, tenant: Tenant, token: str, chat: dict, sender: dict, code: str) -> bool:
    pending = await db.scalar(select(TelegramLink).where(
        TelegramLink.tenant_id == tenant.id, TelegramLink.code == code, TelegramLink.chat_id.is_(None)))
    now = datetime.now(UTC)
    if not pending or not pending.code_expires_at or pending.code_expires_at < now:
        return False
    # une conversation = un seul utilisateur : on remplace un éventuel ancien lien
    await db.execute(delete(TelegramLink).where(
        TelegramLink.tenant_id == tenant.id, TelegramLink.chat_id == chat["id"]))
    pending.chat_id = chat["id"]
    pending.tg_username = sender.get("username")
    pending.tg_name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)[:200] or None
    pending.code = None
    pending.code_expires_at = None
    pending.linked_at = now
    pending.is_active = True
    await db.commit()
    user = await db.scalar(select(User).where(User.id == pending.user_id))
    await send_text(token, chat["id"],
                    f"✅ Compte relié : {user.full_name} — {tenant.name}.\n\n"
                    "Je peux consulter et créer factures, devis, clients, achats, encaisser des paiements "
                    "et vous envoyer les PDF. Écrivez-moi normalement ou utilisez le menu.", MENU)
    return True


async def _context(db: AsyncSession, tenant: Tenant, token: str, chat_id: int) -> Ctx | None:
    """Contexte d'une conversation liée et toujours autorisée, sinon None."""
    link = await db.scalar(select(TelegramLink).where(
        TelegramLink.tenant_id == tenant.id, TelegramLink.chat_id == chat_id, TelegramLink.is_active.is_(True)))
    if not link:
        return None
    user = await db.scalar(select(User).where(User.id == link.user_id, User.is_active.is_(True)))
    if not user:
        return None
    # même mécanisme que l'interface pour un utilisateur travaillant sur un autre dossier
    user = await _apply_active_dossier(db, user, str(tenant.id))
    if user.tenant_id != tenant.id:
        return None
    # Détachés de la session : un rollback (après une action refusée) expirerait
    # sinon leurs attributs, et la suite du traitement ne pourrait plus les lire.
    for obj in (user, tenant):
        if obj in db:
            db.expunge(obj)
    return Ctx(token=token, chat_id=chat_id, db=db, user=user, tenant=tenant)


async def handle_update(tenant_id: int, token: str, update: dict) -> None:
    callback = update.get("callback_query")
    message = update.get("message") or (callback or {}).get("message")
    if not message:
        return
    chat = message["chat"]
    chat_id = chat["id"]
    if callback:
        try:
            await tg_call(token, "answerCallbackQuery", callback_query_id=callback["id"])
        except TelegramError:
            pass
    if chat.get("type") != "private":
        if not callback:
            await send_text(token, chat_id, "Pour protéger vos données, je ne réponds qu'en conversation privée.")
        return

    async with SessionLocal() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if not tenant:
            return
        text = (message.get("text") or "").strip() if not callback else ""

        if text.startswith("/start") and len(text.split()) > 1:
            if not await _link_chat(db, tenant, token, chat, message.get("from") or {}, text.split()[1]):
                await send_text(token, chat_id, "⚠️ Code invalide ou expiré. Générez-en un nouveau dans "
                                                "LIBFAD → Bot Telegram.")
            return

        ctx = await _context(db, tenant, token, chat_id)
        if ctx is None:
            await send_text(token, chat_id, NOT_LINKED)
            return

        state = _wizards.get(ctx.key)
        if state and time.time() - state["ts"] > WIZARD_TTL:
            _wizards.pop(ctx.key, None)
            state = None

        try:
            if callback:
                data = callback.get("data") or ""
                if data == "x":
                    await cmd_cancel(ctx, "")
                elif data.startswith("w:"):
                    await wizard_input(ctx, data[2:])
                elif data.startswith("c:"):
                    await run_command(ctx, data[2:])
                elif data.startswith("imp:"):
                    await confirm_document(ctx, data == "imp:ok")
            elif message.get("document"):
                await handle_document(ctx, message["document"])
            elif text.startswith("/"):
                await run_command(ctx, text)
            elif state and text:
                await wizard_input(ctx, text)
            elif text:
                await handle_free_text(ctx, text)
            else:
                await ctx.send("Je lis le texte et les relevés PDF/CSV. Tapez /aide.")
        except TelegramError:
            raise
        except Exception:
            await db.rollback()
            logger.exception("Erreur pendant le traitement d'un message Telegram (société %s)", tenant_id)
            await send_text(token, chat_id, "⚠️ Une erreur est survenue de mon côté. Réessayez.")


# --- Boucles de polling (une par société) -------------------------------------

_pollers: dict[int, asyncio.Task] = {}
_status: dict[int, dict] = {}
_tasks: set[asyncio.Task] = set()
_chat_locks: dict[tuple[int, int], asyncio.Lock] = {}


def poller_status(tenant_id: int) -> dict:
    return _status.get(tenant_id, {"state": "stopped", "detail": "Bot non démarré"})


def _set_status(tenant_id: int, state: str, detail: str) -> None:
    if _status.get(tenant_id, {}).get("detail") != detail:
        logger.info("Bot Telegram société %s : %s", tenant_id, detail)
    _status[tenant_id] = {"state": state, "detail": detail, "at": datetime.now(UTC)}


async def _dispatch(tenant_id: int, token: str, update: dict) -> None:
    message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    lock = _chat_locks.setdefault((tenant_id, chat_id), asyncio.Lock())
    async with lock:  # les messages d'une même conversation sont traités dans l'ordre
        try:
            await handle_update(tenant_id, token, update)
        except TelegramError as exc:
            logger.warning("Envoi Telegram impossible (société %s) : %s", tenant_id, exc.description)
        except Exception:
            logger.exception("Mise à jour Telegram non traitée (société %s)", tenant_id)


async def _poll(tenant_id: int, token: str) -> None:
    offset = None
    backoff = 1
    try:
        await tg_call(token, "deleteWebhook")  # polling et webhook sont exclusifs
    except TelegramError:
        pass
    while True:
        try:
            updates = await tg_call(
                token, "getUpdates", http_timeout=LONG_POLL_SECONDS + 15, offset=offset,
                timeout=LONG_POLL_SECONDS, allowed_updates=["message", "callback_query"])
        except TelegramError as exc:
            if exc.code in (401, 404):
                _set_status(tenant_id, "error", "Jeton refusé par Telegram — enregistrez-en un nouveau.")
                return
            if exc.code == 409:
                _set_status(tenant_id, "error", "Une autre instance utilise déjà ce bot (conflit).")
            else:
                _set_status(tenant_id, "error", f"Connexion à Telegram perdue : {exc.description}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        backoff = 1
        _set_status(tenant_id, "running", "Connecté — en écoute des messages")
        for update in updates or []:
            offset = update["update_id"] + 1
            task = asyncio.create_task(_dispatch(tenant_id, token, update))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)


async def stop_poller(tenant_id: int) -> None:
    task = _pollers.pop(tenant_id, None)
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _status.pop(tenant_id, None)


async def start_poller(tenant_id: int, token: str) -> None:
    await stop_poller(tenant_id)
    if not settings.telegram_polling_enabled:
        _set_status(tenant_id, "stopped", "Polling désactivé (TELEGRAM_POLLING_ENABLED=false)")
        return
    _set_status(tenant_id, "starting", "Connexion à Telegram…")
    _pollers[tenant_id] = asyncio.create_task(_poll(tenant_id, token), name=f"telegram-poll-{tenant_id}")


async def start_all() -> None:
    """Démarre le bot de chaque société qui en a configuré un. Appelé au démarrage de l'app."""
    if not settings.telegram_polling_enabled:
        return
    async with SessionLocal() as db:
        rows = await db.execute(select(Tenant.id, Tenant.telegram_bot_token).where(
            Tenant.telegram_bot_token.is_not(None)))
        for tenant_id, token in rows.all():
            await start_poller(tenant_id, token)


async def stop_all() -> None:
    for tenant_id in list(_pollers):
        await stop_poller(tenant_id)
    global _http
    if _http is not None and not _http.is_closed:
        await _http.aclose()
    _http = None


async def register_commands(token: str) -> None:
    await tg_call(token, "setMyCommands",
                  commands=[{"command": c, "description": d} for c, d in BOT_COMMANDS])
