"""Pages légales publiques (CGU, CGV, confidentialité, mentions légales).

⚠️ Ce sont des GABARITS de départ, à faire relire/valider par un juriste avant
mise en production. Les infos de l'éditeur sont centralisées dans COMPANY —
modifie-les à un seul endroit (ou branche-les sur une config plus tard).
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# --- Infos de l'éditeur du service (à ajuster) ------------------------------
COMPANY = {
    "name": "LIBENS CONSULTING SARL A.U.",
    "product": "LIBFAD",
    "capital": "100 000,00 DHS",
    "rc": "115885 (Tribunal de commerce de Rabat)",
    "ice": "001234567890123",
    "cnss": "4765684",
    "address": "IM 30 APT 07 3ETG ANGLE RUE MLY ABDELHAFID ET, Av. Youssef Ben Tachfine, 10020 Rabat, Maroc",
    "email": "contact@libfad.ma",
    "host": "À compléter (nom et adresse de l'hébergeur)",
    "dpo_email": "privacy@libfad.ma",
}

_PAGES = {
    "cgu": ("Conditions Générales d'Utilisation", "legal/cgu.html"),
    "cgv": ("Conditions Générales de Vente", "legal/cgv.html"),
    "confidentialite": ("Politique de confidentialité", "legal/confidentialite.html"),
    "mentions": ("Mentions légales", "legal/mentions.html"),
}


def _render(request: Request, slug: str) -> HTMLResponse:
    title, tpl = _PAGES[slug]
    return templates.TemplateResponse(
        tpl,
        {
            "request": request,
            "settings": settings,
            "company": COMPANY,
            "page_title": title,
            "updated": date.today().strftime("%d/%m/%Y"),
        },
    )


@router.get("/legal/cgu", response_class=HTMLResponse)
async def cgu(request: Request):
    return _render(request, "cgu")


@router.get("/legal/cgv", response_class=HTMLResponse)
async def cgv(request: Request):
    return _render(request, "cgv")


@router.get("/legal/confidentialite", response_class=HTMLResponse)
async def confidentialite(request: Request):
    return _render(request, "confidentialite")


@router.get("/legal/mentions", response_class=HTMLResponse)
async def mentions(request: Request):
    return _render(request, "mentions")
