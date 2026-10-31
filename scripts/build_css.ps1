# Recompile le CSS Tailwind (sans Node) via le binaire standalone.
# Usage : pwsh scripts/build_css.ps1   (depuis la racine du projet)
# Le binaire tools/tailwindcss.exe est ignoré par git : le retélécharger si absent depuis
# https://github.com/tailwindlabs/tailwindcss/releases (tailwindcss-windows-x64.exe).
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$bin = Join-Path $root "tools\tailwindcss.exe"
if (-not (Test-Path $bin)) {
    Write-Error "tools\tailwindcss.exe introuvable. Télécharge le binaire standalone Tailwind v3."
}
& $bin -i (Join-Path $root "app\static\css\tailwind-input.css") `
       -o (Join-Path $root "app\static\css\tailwind.css") `
       --minify
Write-Host "CSS Tailwind recompilé -> app/static/css/tailwind.css"
