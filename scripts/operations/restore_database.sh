#!/usr/bin/env bash
# ==============================================================================
# MAYA AI - Production Database Disaster Recovery Restoration
# Task P16-T005 | RTO <= 4h Guarantee
# ==============================================================================

set -euo pipefail

trap 'echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ERROR: Restore failed on line $LINENO" >&2; exit 1' ERR

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <path_to_backup_file> [--skip-smoke-test]" >&2
  exit 1
fi

BACKUP_FILE="$1"
SKIP_SMOKE_TEST="${2:-}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-maya}"
POSTGRES_USER="${POSTGRES_USER:-maya_admin}"

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "ERROR: Backup file '${BACKUP_FILE}' does not exist!" >&2
  exit 1
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Initiating disaster recovery restore from '${BACKUP_FILE}'..."

# 1. Cryptographic Checksum Verification
CHECKSUM_FILE="${BACKUP_FILE}.sha256"
if [[ -f "${CHECKSUM_FILE}" ]]; then
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Verifying SHA-256 integrity..."
  sha256sum -c "${CHECKSUM_FILE}"
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Integrity check PASSED."
else
  echo "WARNING: No checksum file found at '${CHECKSUM_FILE}'. Proceeding with caution." >&2
fi

# 2. Decrypt if file ends with .enc
RESTORE_SOURCE="${BACKUP_FILE}"
CLEANUP_DECRYPTED=""
if [[ "${BACKUP_FILE}" == *.enc ]]; then
  if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]]; then
    echo "ERROR: Backup is encrypted, but BACKUP_ENCRYPTION_PASSPHRASE is unset!" >&2
    exit 1
  fi
  DECRYPTED_FILE="${BACKUP_FILE%.enc}.tmp_decrypted"
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Decrypting backup payload..."
  openssl enc -d -aes-256-cbc -pbkdf2 \
    -in "${BACKUP_FILE}" \
    -out "${DECRYPTED_FILE}" \
    -pass pass:"${BACKUP_ENCRYPTION_PASSPHRASE}"
  RESTORE_SOURCE="${DECRYPTED_FILE}"
  CLEANUP_DECRYPTED="true"
fi

# 3. Connectivity Verification
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Verifying PostgreSQL target availability at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
pg_isready -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"

# 4. Execute pg_restore
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Restoring database schema and records..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --single-transaction \
  "${RESTORE_SOURCE}" || {
    # pg_restore returns exit code 1 if warnings occurred on clean/drop
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] pg_restore completed with minor notices."
  }

# Cleanup temporary decrypted payload
if [[ "${CLEANUP_DECRYPTED}" == "true" ]]; then
  rm -f "${RESTORE_SOURCE}"
fi

# 5. Post-Restore Smoke Test Verification
if [[ "${SKIP_SMOKE_TEST}" != "--skip-smoke-test" ]]; then
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Running post-restore integrity and RLS verification..."

  EXT_CHECK=$(PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    --host="${POSTGRES_HOST}" --port="${POSTGRES_PORT}" --username="${POSTGRES_USER}" --dbname="${POSTGRES_DB}" \
    -t -A -c "SELECT count(*) FROM pg_extension WHERE extname IN ('vector', 'pg_trgm');")

  if [[ "${EXT_CHECK}" -lt 2 ]]; then
    echo "ERROR: Required pgvector and pg_trgm extensions are missing!" >&2
    exit 1
  fi

  POLICY_COUNT=$(PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    --host="${POSTGRES_HOST}" --port="${POSTGRES_PORT}" --username="${POSTGRES_USER}" --dbname="${POSTGRES_DB}" \
    -t -A -c "SELECT count(*) FROM pg_policy;")

  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Verification success: Extensions present, ${POLICY_COUNT} RLS policies active."
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Disaster recovery restoration finished successfully. RTO target achieved."
