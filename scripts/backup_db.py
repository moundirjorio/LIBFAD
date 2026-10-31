"""Sauvegarde de la base PostgreSQL (pg_dump), horodatée, avec rotation.

Usage :
    python -m scripts.backup_db
    python -m scripts.backup_db --keep 14 --out ./backups

Nécessite `pg_dump` accessible dans le PATH (fourni avec PostgreSQL).
À planifier quotidiennement (Tâche planifiée Windows / cron Linux).
En production, copier ensuite le fichier vers un stockage distant (S3, etc.).
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings


def _pg_conn_params() -> dict:
    """Extrait host/port/user/password/dbname depuis DATABASE_URL_SYNC."""
    u = urlparse(settings.database_url_sync.replace("+psycopg2", ""))
    return {
        "host": u.hostname or "localhost",
        "port": str(u.port or 5432),
        "user": u.username or "",
        "password": u.password or "",
        "dbname": (u.path or "/").lstrip("/") or "postgres",
    }


def backup(out_dir: Path, keep: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = _pg_conn_params()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw = out_dir / f"libfad_{p['dbname']}_{stamp}.sql"
    gz = raw.with_suffix(".sql.gz")

    if shutil.which("pg_dump") is None:
        sys.exit("pg_dump introuvable dans le PATH. Installe les outils clients PostgreSQL.")

    env = {"PGPASSWORD": p["password"]}
    cmd = [
        "pg_dump", "-h", p["host"], "-p", p["port"], "-U", p["user"],
        "-d", p["dbname"], "--no-owner", "--no-privileges", "-f", str(raw),
    ]
    import os

    subprocess.run(cmd, check=True, env={**os.environ, **env})

    # Compression gzip puis suppression du .sql brut.
    with open(raw, "rb") as fin, gzip.open(gz, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    raw.unlink(missing_ok=True)

    # Rotation : ne garder que les `keep` sauvegardes les plus récentes.
    backups = sorted(out_dir.glob("libfad_*.sql.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)

    return gz


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./backups", help="Dossier de destination")
    ap.add_argument("--keep", type=int, default=14, help="Nombre de sauvegardes à conserver")
    args = ap.parse_args()
    path = backup(Path(args.out).resolve(), args.keep)
    print(f"Sauvegarde créée : {path} ({path.stat().st_size} octets)")
