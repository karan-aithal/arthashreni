#!/usr/bin/env bash
# One-time PostgreSQL setup for ArthaShreni (ADR-0004, ADR-0005, ADR-0014).
# Creates:
#   artha_owner  - owns the database and schemas; runs migrations; may create the restore-test DB
#   artha_demo   - app role for the DEMO dataset (schema grants come from migrations)
#   artha_real   - app role for the REAL dataset (schema grants come from migrations)
#   arthashreni  - the project database
# Passwords are random, written only to .env (mode 600, git-ignored).
# Run from the repo root:  bash scripts/db_setup.sh   (asks for your sudo password once)
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
DB=arthashreni
HOST=127.0.0.1
PORT=5432

if [ -f "$ENV_FILE" ] && grep -q '^ARTHA_DB_OWNER_URL=' "$ENV_FILE"; then
  echo "$ENV_FILE already has database settings; refusing to overwrite. Remove those lines to re-run." >&2
  exit 1
fi

pw_owner=$(openssl rand -hex 24)
pw_demo=$(openssl rand -hex 24)
pw_real=$(openssl rand -hex 24)

# SQL goes through stdin so passwords never appear in the process list.
sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d postgres <<SQL
-- pragma: allowlist nextline secret
SET password_encryption = 'scram-sha-256';
SELECT format('CREATE ROLE artha_owner LOGIN CREATEDB PASSWORD %L', '$pw_owner')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artha_owner') \gexec
SELECT format('CREATE ROLE artha_demo LOGIN PASSWORD %L', '$pw_demo')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artha_demo') \gexec
SELECT format('CREATE ROLE artha_real LOGIN PASSWORD %L', '$pw_real')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artha_real') \gexec
-- If the roles already existed, reset passwords so they match the new .env.
ALTER ROLE artha_owner PASSWORD '$pw_owner';
ALTER ROLE artha_demo  PASSWORD '$pw_demo';
ALTER ROLE artha_real  PASSWORD '$pw_real';
SELECT 'CREATE DATABASE $DB OWNER artha_owner ENCODING ''UTF8'' TEMPLATE template0'
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$DB') \gexec
REVOKE ALL ON DATABASE $DB FROM PUBLIC;
GRANT CONNECT ON DATABASE $DB TO artha_owner, artha_demo, artha_real;
\c $DB
-- Nobody but the owner may create objects in public; app roles get only what migrations grant.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO artha_owner;
SQL

umask 077
{
  echo "# PostgreSQL (written by scripts/db_setup.sh $(date -u +%F))"
  echo "ARTHA_DB_OWNER_URL=postgresql+psycopg://artha_owner:$pw_owner@$HOST:$PORT/$DB"
  echo "ARTHA_DB_DEMO_URL=postgresql+psycopg://artha_demo:$pw_demo@$HOST:$PORT/$DB"
  echo "ARTHA_DB_REAL_URL=postgresql+psycopg://artha_real:$pw_real@$HOST:$PORT/$DB"
  echo "ARTHA_DATASET=demo"
} >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Check each role can log in over TCP with its password.
for pair in "artha_owner:$pw_owner" "artha_demo:$pw_demo" "artha_real:$pw_real"; do
  role=${pair%%:*}; pw=${pair#*:}
  PGPASSWORD=$pw psql -h "$HOST" -p "$PORT" -U "$role" -d "$DB" -Atc "select current_user" >/dev/null
  echo "ok: $role can connect to $DB"
done
echo "Done. Credentials are in $ENV_FILE (mode 600, git-ignored)."
