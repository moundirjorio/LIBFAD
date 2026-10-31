# LIBFAD — Démarrage rapide

## 1. Installer (1 fois)

```powershell
# Depuis le dossier du projet
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configurer la base PostgreSQL

**Option A — Script automatique** (recommandé, fournis le mot de passe `postgres`) :

```powershell
.\scripts\setup.ps1 -PostgresPassword "TON_MOT_DE_PASSE_POSTGRES"
```

**Option B — Manuel** :

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE USER libfad WITH PASSWORD 'libfad';"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE libfad OWNER libfad ENCODING 'UTF8';"
.\.venv\Scripts\python.exe -m scripts.init_db
```

## 3. Configurer Claude

Édite `.env` et renseigne :
```
ANTHROPIC_API_KEY=sk-ant-...
```
(récupère ta clé sur https://console.anthropic.com)

## 4. (Optionnel) Créer des données de démo

```powershell
.\.venv\Scripts\python.exe -m scripts.create_demo
# Login : demo@libfad.local / demo1234
```

## 5. Lancer le serveur

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

Ouvre http://localhost:8000

## 6. Tâches planifiées (relances, abonnements)

LIBFAD expose un endpoint à appeler quotidiennement par un cron / Windows Task Scheduler :

```
POST /api/dunning/daily
```

Il :
- crée les factures récurrentes échues (abonnements)
- marque les factures en retard
- exécute les étapes de relance arrivées à échéance (génération email IA + envoi)

Exemple cron Linux :
```cron
0 8 * * * curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/dunning/daily
```

Sous Windows (Task Scheduler) : créer une tâche qui appelle `Invoke-RestMethod` avec le bon token.

---

## Stack technique

| Couche | Techno |
|---|---|
| Backend | FastAPI (async) |
| ORM | SQLAlchemy 2 + asyncpg |
| Migrations | Alembic |
| DB | PostgreSQL 18 |
| Frontend | Jinja2 + HTMX + Alpine.js + Tailwind (CDN) |
| IA | Claude (anthropic SDK Python) |
| OCR | Claude Vision (PDF + images) |
| PDF | WeasyPrint |
| Email | aiosmtplib |
| Auth | JWT (cookie `libfad_token`) |
| Excel | openpyxl |

## Modules livrés

- ✅ Multi-tenant (chaque entreprise est isolée)
- ✅ Facturation : devis, factures, items, multi-TVA, conversion devis→facture
- ✅ Numérotation automatique conforme (FA-YYYY-NNNNN)
- ✅ Paiements partiels + acomptes
- ✅ Génération PDF (factures + devis) avec branding tenant
- ✅ Abonnements / factures récurrentes
- ✅ CRM : clients, contacts multiples, scoring solvabilité
- ✅ Relances : scénarios configurables + génération emails IA Claude
- ✅ Chat IA : assistant conversationnel avec contexte métier
- ✅ OCR factures fournisseurs (Claude Vision)
- ✅ Rapports : CA, balance âgée, top clients, anomalies, prédictions
- ✅ Export Excel
- ✅ UI dashboard complète (Tailwind)
- ✅ Auth + multi-locale (FR/MA, EUR/MAD)

## Tests

```powershell
.\.venv\Scripts\pytest.exe
```

## Génération de migrations

Une fois la base initialisée :
```powershell
.\.venv\Scripts\alembic.exe revision --autogenerate -m "ma migration"
.\.venv\Scripts\alembic.exe upgrade head
```
