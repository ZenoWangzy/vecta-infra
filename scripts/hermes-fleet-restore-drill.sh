#!/usr/bin/env bash
set -euo pipefail

EXECUTE=false
BACKUP_DIR=""

usage() {
  echo "Usage: scripts/hermes-fleet-restore-drill.sh --backup-dir PATH [--execute]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE=true; shift ;;
    --backup-dir) BACKUP_DIR="${2:?--backup-dir requires a path}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$BACKUP_DIR" ] || { usage >&2; exit 2; }
resolved_backup="$(realpath -e "$BACKUP_DIR")"
case "$resolved_backup" in
  /data/ocee/backups/hermes-fleet-[0-9]*T[0-9]*Z) ;;
  *) echo "backup must be a completed Hermes fleet backup" >&2; exit 2 ;;
esac
[ -f "$resolved_backup/COMPLETE" ] && [ -f "$resolved_backup/SHA256SUMS" ] || {
  echo "backup completion evidence is missing" >&2
  exit 1
}
(
  cd "$resolved_backup"
  sha256sum --check --quiet SHA256SUMS
)

source_state="$(find "$resolved_backup/items" -mindepth 2 -maxdepth 2 -type d -name state | LC_ALL=C sort | head -n 1)"
[ -n "$source_state" ] || {
  echo "backup contains no captured container state" >&2
  exit 1
}

if [ "$EXECUTE" != true ]; then
  echo "restore drill preflight passed; pass --execute to restore and compare one item"
  exit 0
fi

drill_root="$(mktemp -d /data/ocee/backups/.hermes-restore-drill.XXXXXX)"
cleanup() {
  case "$drill_root" in
    /data/ocee/backups/.hermes-restore-drill.*) rm -rf -- "$drill_root" ;;
    *) echo "refusing unsafe restore-drill cleanup" >&2 ;;
  esac
}
trap cleanup EXIT

install -d -m 0770 "$drill_root/restored"
cp -a -- "$source_state/." "$drill_root/restored/"
diff -qr --no-dereference "$source_state" "$drill_root/restored" >/dev/null

source_digest="$(tar -C "$source_state" --sort=name --mtime='UTC 1970-01-01' \
  --owner=0 --group=0 --numeric-owner -cf - . | sha256sum | cut -d ' ' -f 1)"
restored_digest="$(tar -C "$drill_root/restored" --sort=name --mtime='UTC 1970-01-01' \
  --owner=0 --group=0 --numeric-owner -cf - . | sha256sum | cut -d ' ' -f 1)"
[ "$source_digest" = "$restored_digest" ] || {
  echo "restored state digest mismatch" >&2
  exit 1
}

printf 'status=success\nsource_item=%s\nstate_digest=%s\nverified_at=%s\n' \
  "$(basename "$(dirname "$source_state")")" "$source_digest" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$resolved_backup/RESTORE_DRILL"
(
  cd "$resolved_backup"
  find . -type f ! -name SHA256SUMS -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum --check --quiet SHA256SUMS
)
echo "restore_drill=success"
