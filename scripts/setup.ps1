# Setup LIBFAD sous Windows
# Usage : .\scripts\setup.ps1 -PostgresPassword "<mot-de-passe-postgres>"
param(
    [Parameter(Mandatory=$true)][string]$PostgresPassword,
    [string]$DbUser = "libfad",
    [string]$DbPassword = "libfad",
    [string]$DbName = "libfad"
)

$ErrorActionPreference = "Stop"
$PsqlPath = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
if (-not (Test-Path $PsqlPath)) {
    Write-Error "psql introuvable : $PsqlPath"
    exit 1
}

Write-Host "→ Création de l'utilisateur PostgreSQL '$DbUser'…"
$env:PGPASSWORD = $PostgresPassword
& $PsqlPath -U postgres -d postgres -c "DO `$`$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$DbUser') THEN CREATE USER $DbUser WITH PASSWORD '$DbPassword'; END IF; END `$`$;"

Write-Host "→ Création de la base '$DbName'…"
$exists = & $PsqlPath -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName'"
if ($exists -ne "1") {
    & $PsqlPath -U postgres -d postgres -c "CREATE DATABASE $DbName OWNER $DbUser ENCODING 'UTF8';"
} else {
    Write-Host "   Base existante, on saute."
}

Remove-Item Env:PGPASSWORD

Write-Host "→ Mise à jour de .env si besoin…"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host "→ Création des tables (SQLAlchemy create_all)…"
& .\.venv\Scripts\python.exe -m scripts.init_db

Write-Host ""
Write-Host "✔ Setup terminé !" -ForegroundColor Green
Write-Host ""
Write-Host "Prochaines étapes :"
Write-Host "  1. Éditer .env et renseigner ANTHROPIC_API_KEY"
Write-Host "  2. (Optionnel) Créer un jeu de données démo :"
Write-Host "       .\.venv\Scripts\python.exe -m scripts.create_demo"
Write-Host "  3. Lancer le serveur :"
Write-Host "       .\.venv\Scripts\uvicorn.exe app.main:app --reload"
Write-Host "  4. Ouvrir http://localhost:8000"
