#!/usr/bin/env bash
# Write a deploy lifecycle audit entry directly to audit_log via psql.
# usage: write-deploy-audit.sh <start|success|failure|rollback> <sha> [resource]
# Requires: DATABASE_URL env var set to the vtest postgres connection string.
set -euo pipefail
stage=$1; sha=$2; resource=${3:-vtest}
severity=$([[ "$stage" == "failure" || "$stage" == "rollback" ]] && echo warn || echo info)
if [ "$(psql "$DATABASE_URL" -tAc "select coalesce(to_regclass('audit_log')::text, '')")" != "audit_log" ]; then
  echo "SKIP deploy audit: audit_log table is absent"
  exit 0
fi
psql "$DATABASE_URL" -c \
  "INSERT INTO audit_log (event_type, resource, severity, payload, created_at)
   VALUES ('deploy.${stage}', '${resource}', '${severity}',
           '{\"sha\":\"${sha}\",\"source\":\"ci-deploy\"}'::jsonb, now())"
