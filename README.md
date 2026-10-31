# LIBFAD — Agent IA Administratif & Facturation

Plateforme SaaS d'automatisation de la gestion administrative et de la facturation, propulsée par Claude (Anthropic).

## Stack

- **Backend**: FastAPI (Python 3.11+)
- **ORM**: SQLAlchemy 2 (async) + Alembic
- **DB**: PostgreSQL 18
- **Frontend**: Jinja2 + HTMX + Tailwind (CDN)
- **IA**: Claude (anthropic SDK) — chat, génération d'emails, OCR vision
- **PDF**: WeasyPrint
- **Email**: fastapi-mail (SMTP)

## Modules

| Module | Description |
|---|---|
| Facturation | Devis, factures, acomptes, récurrentes, multi-TVA, PDF |
| CRM | Clients, contacts, historique, score de solvabilité |
| Relances IA | Détection retards, scénarios multilingues, emails Claude |
| Chat IA | Assistant conversationnel sur les données du tenant |
| OCR | Lecture factures fournisseurs via Claude Vision |
| Rapports | Tableaux de bord, analyse prédictive des retards |

## Installation rapide (Windows)

```powershell
# 1. Créer l'environnement virtuel
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Configurer l'environnement
Copy-Item .env.example .env
# éditer .env : DATABASE_URL, ANTHROPIC_API_KEY, SMTP_*

# 4. Créer la base PostgreSQL
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE USER libfad WITH PASSWORD 'libfad';"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE libfad OWNER libfad;"

# 5. Appliquer les migrations
alembic upgrade head

# 6. Lancer le serveur
uvicorn app.main:app --reload
```

Application disponible sur http://localhost:8000

### Note WeasyPrint sous Windows
WeasyPrint a besoin des libs GTK. Si l'install échoue, suivre :
https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows
