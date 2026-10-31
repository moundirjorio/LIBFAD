# Déploiement de LIBFAD

## En local (test rapide avec Docker)

```bash
docker compose up --build
```
- L'app démarre sur http://localhost:8000
- Le service `db` (PostgreSQL) est créé automatiquement.
- Les migrations Alembic sont appliquées au démarrage (`docker-entrypoint.sh`).
- Données persistées dans les volumes Docker `db_data` et `storage`.

## En production (VPS)

1. **Installer** Docker + Docker Compose sur le serveur.
2. **Copier le projet** puis créer le `.env` :
   ```bash
   cp .env.production.example .env
   # éditer .env : secrets aléatoires, BASE_URL, DB_PASSWORD, SMTP, Stripe…
   ```
3. **Lancer** :
   ```bash
   docker compose up -d --build
   ```
4. **Reverse-proxy HTTPS** (Caddy / Nginx / Traefik) devant le port 8000, avec ton
   nom de domaine et un certificat TLS (Let's Encrypt). Une fois en HTTPS, garder
   `COOKIE_SECURE=true` dans `.env`.

## Sauvegardes

Planifier chaque nuit (cron) :
```bash
docker compose exec -T db pg_dump -U libfad libfad | gzip > backup_$(date +%F).sql.gz
```
(ou, hors conteneur, `python -m scripts.backup_db` avec `pg_dump` dans le PATH).
Copier ensuite les sauvegardes vers un stockage distant (S3).

## Mises à jour de schéma

Les migrations s'appliquent automatiquement au démarrage du conteneur `app`.
Pour créer une migration après un changement de modèle :
```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Points de sécurité avant ouverture au public
- [ ] `SECRET_KEY` et `JWT_SECRET` aléatoires (pas les valeurs d'exemple)
- [ ] `COOKIE_SECURE=true` + HTTPS
- [ ] Mot de passe DB fort (`DB_PASSWORD`)
- [ ] SMTP réel configuré (emails)
- [ ] Sauvegardes planifiées + testées (restauration)
- [ ] CGU/CGV/confidentialité relues par un juriste
