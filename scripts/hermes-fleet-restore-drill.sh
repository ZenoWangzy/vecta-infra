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
for required_file in MANIFEST COMPLETE fleet-rows.base64.tsv; do
  test -s "$resolved_backup/$required_file" || {
    echo "backup is missing required file: $required_file" >&2
    exit 1
  }
done
find "$resolved_backup" -type f -print0 |
  while IFS= read -r -d '' archive_file; do
    test -r "$archive_file" || {
      echo "backup archive contains an unreadable file: $archive_file" >&2
      exit 1
    }
  done

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

source_file_count="$(find "$source_state" -type f -print | wc -l | tr -d ' ')"
restored_file_count="$(find "$drill_root/restored" -type f -print | wc -l | tr -d ' ')"
[ "$source_file_count" = "$restored_file_count" ] || {
  echo "restored state file count mismatch" >&2
  exit 1
}

printf 'status=success\nsource_item=%s\nfile_count=%s\nverified_at=%s\n' \
  "$(basename "$(dirname "$source_state")")" "$source_file_count" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$resolved_backup/RESTORE_DRILL"
test -s "$resolved_backup/RESTORE_DRILL"
echo "restore_drill=success"
