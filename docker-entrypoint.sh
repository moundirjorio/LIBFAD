#!/bin/sh
set -e

# Attente de la base de données (max ~60s).
echo "→ Attente de la base de données…"
python - <<'PY'
import os, socket, time
from urllib.parse import urlparse
u = urlparse(os.environ.get("DATABASE_URL_SYNC", "").replace("+psycopg2", ""))
host, port = (u.hostname or "db"), (u.port or 5432)
for _ in range(60):
    try:
        socket.create_connection((host, port), timeout=2).close()
        print(f"  base joignable ({host}:{port})")
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("Base de données injoignable.")
PY

# Applique les migrations (crée le schéma complet sur une base vierge,
# ou évolue une base existante). La migration initiale gère les FK circulaires.
echo "→ Application des migrations Alembic…"
alembic upgrade head

echo "→ Démarrage du serveur."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}"
