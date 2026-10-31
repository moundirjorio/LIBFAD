"""Crée toutes les tables via SQLAlchemy (rapide pour le dev).

Pour le prod : préférer `alembic upgrade head` après avoir généré une révision.
Usage : `python -m scripts.init_db`
"""

import asyncio

from app.database import Base, engine
import app.models  # noqa: F401  enregistre tous les modèles


async def main() -> None:
    print("→ Création des tables…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✔ Tables créées (ou déjà existantes).")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
