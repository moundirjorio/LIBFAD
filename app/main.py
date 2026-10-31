import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.background import shutdown_scheduler, start_scheduler
from app.config import settings
from app.services import telegram_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("libfad")
from app.routers import (
    accounting,
    auth,
    bank,
    billing,
    chat,
    clients,
    dashboard,
    documents,
    dossiers,
    dunning,
    invoices,
    legal,
    payments,
    portal,
    products,
    quotes,
    reports,
    search,
    subscriptions,
    suppliers,
    telegram,
    tenants,
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_path  # ensures storage dir exists
    # Sécurité : refuse de démarrer en production avec des secrets d'exemple.
    insecure = settings.insecure_secrets
    if insecure:
        msg = f"Secrets non sécurisés (valeurs d'exemple) : {', '.join(insecure)}"
        if settings.app_env == "production":
            raise RuntimeError(msg + " — définis-les dans .env avant de démarrer en production.")
        logger.warning("%s — à durcir avant la mise en production.", msg)
    start_scheduler()
    await telegram_bot.start_all()
    try:
        yield
    finally:
        await telegram_bot.stop_all()
        shutdown_scheduler()


app = FastAPI(
    title="LIBFAD — Agent IA Administratif & Facturation",
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)

from app.csrf import CSRFMiddleware

app.add_middleware(CSRFMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.cookie_secure,
    same_site=settings.cookie_samesite,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# Pages HTML (dashboard + auth UI)
app.include_router(dashboard.router)
app.include_router(auth.router)

# Espace client (portail) — interface séparée de l'entreprise
app.include_router(portal.router)

# Multi-dossiers (bascule de société)
app.include_router(dossiers.router)

# Pages légales publiques
app.include_router(legal.router)

# API JSON
app.include_router(tenants.router, prefix="/api/tenants", tags=["tenants"])
app.include_router(clients.router, prefix="/api/clients", tags=["clients"])
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(quotes.router, prefix="/api/quotes", tags=["quotes"])
app.include_router(invoices.router, prefix="/api/invoices", tags=["invoices"])
app.include_router(payments.router, prefix="/api/payments", tags=["payments"])
app.include_router(dunning.router, prefix="/api/dunning", tags=["dunning"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["subscriptions"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(billing.router, prefix="/api/billing", tags=["billing"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(accounting.router, prefix="/api/accounting", tags=["accounting"])
app.include_router(bank.router, prefix="/api/bank", tags=["bank"])
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["suppliers"])
app.include_router(telegram.router, prefix="/api/telegram", tags=["telegram"])


_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="8" fill="#5b57f5"/>'
    '<path d="M11 8h3v13h7v3h-10z" fill="#fff"/></svg>'
)


@app.get("/favicon.ico")
async def favicon():
    return Response(content=_FAVICON, media_type="image/svg+xml")


@app.get("/healthz", response_class=HTMLResponse)
async def healthz(request: Request):
    return HTMLResponse(f"<h1>{settings.app_name} OK</h1>")


# --- Gestion d'erreurs globale --------------------------------------------
_ERROR_TITLES = {
    404: "Page introuvable",
    403: "Accès refusé",
    401: "Connexion requise",
    500: "Erreur interne",
}


def _wants_json(request: Request) -> bool:
    """Les routes API et les appels fetch/XHR reçoivent du JSON, les pages du HTML."""
    if request.url.path.startswith("/api"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return templates.TemplateResponse(
        "errors/error.html",
        {
            "request": request,
            "settings": settings,
            "code": exc.status_code,
            "title": _ERROR_TITLES.get(exc.status_code, "Erreur"),
            "message": exc.detail if isinstance(exc.detail, str) else "Une erreur est survenue.",
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Erreur non gérée sur %s %s", request.method, request.url.path)
    if _wants_json(request):
        return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur."})
    return templates.TemplateResponse(
        "errors/error.html",
        {
            "request": request,
            "settings": settings,
            "code": 500,
            "title": _ERROR_TITLES[500],
            "message": "Une erreur inattendue est survenue. L'équipe a été notifiée.",
        },
        status_code=500,
    )
