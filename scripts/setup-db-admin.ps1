# À LANCER EN POWERSHELL ADMINISTRATEUR
# 1. Redémarre PostgreSQL (auth "trust" déjà appliquée à pg_hba.conf)
# 2. Crée l'utilisateur libfad et la base libfad
# 3. Réinitialise le mot de passe postgres à "postgres"
# 4. Restaure pg_hba.conf en mode scram-sha-256
# 5. Redémarre PostgreSQL

$ErrorActionPreference = "Stop"

$pgData = "C:\Program Files\PostgreSQL\18\data"
$hba = Join-Path $pgData "pg_hba.conf"
$backup = Join-Path $pgData "pg_hba.conf.libfad-backup"
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"

Write-Host "Etape 1/5 : Verification du backup pg_hba.conf"
if (-not (Test-Path $backup)) {
    Write-Error "Backup introuvable. Annulation."
    exit 1
}

Write-Host "Etape 2/5 : Redemarrage du service PostgreSQL (auth trust temporaire)"
Restart-Service postgresql-x64-18 -Force
Start-Sleep -Seconds 3

Write-Host "Etape 3/5 : Creation utilisateur libfad et base libfad"
& $psql -U postgres -h localhost -p 5433 -d postgres -c "CREATE USER libfad WITH PASSWORD 'libfad';" 2>&1 | Out-Host
& $psql -U postgres -h localhost -p 5433 -d postgres -c "CREATE DATABASE libfad OWNER libfad ENCODING 'UTF8';" 2>&1 | Out-Host
& $psql -U postgres -h localhost -p 5433 -d postgres -c "ALTER USER postgres WITH PASSWORD 'postgres';" 2>&1 | Out-Host

Write-Host "Etape 4/5 : Restauration pg_hba.conf securise"
Copy-Item $backup $hba -Force

Write-Host "Etape 5/5 : Redemarrage final du service PostgreSQL"
Restart-Service postgresql-x64-18 -Force
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "OK - Setup base de donnees termine !" -ForegroundColor Green
Write-Host ""
Write-Host "Credentiels :" -ForegroundColor Cyan
Write-Host "  postgres / postgres  (admin Postgres)"
Write-Host "  libfad   / libfad    (utilisateur applicatif)"
Write-Host "  DB : libfad (port 5433)"
Write-Host ""
Write-Host "Continue dans un PowerShell normal (pas admin) avec :"
Write-Host "  cd c:\Users\victus\Desktop\LIBFAD"
Write-Host "  .\.venv\Scripts\python.exe -m scripts.init_db"
Write-Host "  .\.venv\Scripts\python.exe -m scripts.create_demo"
Write-Host "  .\.venv\Scripts\uvicorn.exe app.main:app --reload"
