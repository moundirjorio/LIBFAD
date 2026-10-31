"""Protection CSRF par vérification d'origine.

Défense en profondeur, en complément des cookies SameSite=Lax :
pour toute requête qui modifie l'état (POST/PUT/PATCH/DELETE), on vérifie que
l'en-tête `Origin` (ou `Referer` à défaut) correspond bien à l'hôte de l'app.
Un site tiers ne peut pas falsifier `Origin` depuis le navigateur de la victime,
donc une soumission de formulaire cross-site est rejetée (403).

Header-only : ne lit pas le corps de la requête (pas de conflit avec le parsing
des formulaires), fonctionne pour les <form> comme pour fetch/XHR, et ne casse
pas les clients non-navigateur (curl, tests) qui n'envoient pas d'Origin.
"""

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                origin_host = urlparse(origin).netloc
                request_host = request.headers.get("host", "")
                if origin_host and origin_host != request_host:
                    if request.url.path.startswith("/api"):
                        return JSONResponse(
                            {"detail": "Origine non autorisée (CSRF)."}, status_code=403
                        )
                    return PlainTextResponse("Origine non autorisée (CSRF).", status_code=403)
        return await call_next(request)
