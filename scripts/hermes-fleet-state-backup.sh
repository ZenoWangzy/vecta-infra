#!/usr/bin/env bash
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/data/ocee/backups}"
DB_CONTAINER="${DB_CONTAINER:-openclaw-postgres}"
DB_USER="${DB_USER:-openclaw_poc}"
DB_NAME="${DB_NAME:-openclaw_poc}"
INSTANCE_ROOT="${INSTANCE_ROOT:-/data/ocee/data/instances}"
SESSION_ID="${SESSION_ID:-hermes-fleet-$(date -u +%Y%m%dT%H%M%SZ)}"
EXECUTE=false

usage() {
  cat <<'EOF'
Usage: scripts/hermes-fleet-state-backup.sh [--execute] [options]

Options:
  --execute             Create the quiesced backup. Without it, preflight only.
  --backup-root PATH    Backup parent (must resolve below /data/ocee/backups).
  --session-id ID       hermes-fleet-YYYYMMDDTHHMMSSZ identifier.
  --instance-root PATH  Fleet host instance-data root.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE=true; shift ;;
    --backup-root) BACKUP_ROOT="${2:?--backup-root requires a path}"; shift 2 ;;
    --session-id) SESSION_ID="${2:?--session-id requires an id}"; shift 2 ;;
    --instance-root) INSTANCE_ROOT="${2:?--instance-root requires a path}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! printf '%s' "$SESSION_ID" | grep -Eq '^hermes-fleet-[0-9]{8}T[0-9]{6}Z$'; then
  echo "session id must match hermes-fleet-YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi

for command in docker getfacl setfacl realpath sha256sum base64; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 1
  }
done

resolved_backup_root="$(realpath -m "$BACKUP_ROOT")"
case "$resolved_backup_root" in
  /data/ocee/backups|/data/ocee/backups/*) ;;
  *) echo "backup root must be below /data/ocee/backups" >&2; exit 2 ;;
esac

[ -d "$resolved_backup_root" ] || {
  echo "backup root does not exist: $resolved_backup_root" >&2
  exit 1
}
getfacl -cp "$resolved_backup_root" | grep -qx 'user:shiyao:rwx' || {
  echo "backup root must preserve the approved shiyao rwx ACL" >&2
  exit 1
}
getfacl -cp "$resolved_backup_root" | grep -qx 'default:user:shiyao:rwx' || {
  echo "backup root must preserve the approved default shiyao rwx ACL" >&2
  exit 1
}

final_dir="$resolved_backup_root/$SESSION_ID"
staging_dir="$resolved_backup_root/.${SESSION_ID}.incomplete"
[ ! -e "$final_dir" ] && [ ! -e "$staging_dir" ] || {
  echo "backup target already exists" >&2
  exit 1
}

rows_file="$(mktemp)"
current_container=""
current_was_paused=false

cleanup() {
  if [ -n "$current_container" ] && [ "$current_was_paused" = false ]; then
    docker unpause "$current_container" >/dev/null 2>&1 || true
  fi
  unlink "$rows_file" 2>/dev/null || true
}
trap cleanup EXIT

docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -At -F '|' -c \
  "SELECT encode(convert_to(employee_id, 'UTF8'), 'base64'),
          status, lifecycle, agent_type, COALESCE(container_id, '')
     FROM fleet_instances
    ORDER BY employee_id" > "$rows_file"

row_count=0
running_count=0
nonrunning_count=0
while IFS='|' read -r employee_b64 status lifecycle agent_type container_ref; do
  [ -n "$employee_b64" ] || continue
  row_count=$((row_count + 1))
  if [ "$status" = running ]; then
    running_count=$((running_count + 1))
    [ "$agent_type" = hermes ] || {
      echo "running non-Hermes row blocks this backup" >&2
      exit 1
    }
    [ -n "$container_ref" ] || {
      echo "running row has no container reference" >&2
      exit 1
    }
    docker_state="$(docker inspect --format '{{.State.Status}}' "$container_ref" 2>/dev/null || true)"
    [ "$docker_state" = running ] || {
      echo "running Fleet row does not map to a running Docker container" >&2
      exit 1
    }
    [ "$(docker inspect --format '{{.State.Paused}}' "$container_ref")" = false ] || {
      echo "running Fleet row maps to an unexpectedly paused container" >&2
      exit 1
    }
  else
    nonrunning_count=$((nonrunning_count + 1))
  fi
done < "$rows_file"

printf 'fleet_rows=%s running_hermes=%s nonrunning=%s target=%s\n' \
  "$row_count" "$running_count" "$nonrunning_count" "$final_dir"

if [ "$EXECUTE" != true ]; then
  echo "preflight only; pass --execute to create the backup"
  exit 0
fi

install -d -m 0770 "$staging_dir/items"
cp -- "$rows_file" "$staging_dir/fleet-rows.base64.tsv"

copy_container_state() {
  local container_ref="$1"
  local destination="$2"
  local state_path key
  local -a present_paths=()
  local -a source_paths=(
    /data/openclaw
    /home/node/.openclaw
    /app/config
    /app/skills
    /app/plugins
    /opt/data
  )

  for state_path in "${source_paths[@]}"; do
    if docker exec "$container_ref" test -e "$state_path"; then
      present_paths+=("$state_path")
    fi
  done

  current_container="$container_ref"
  if [ "$(docker inspect --format '{{.State.Paused}}' "$container_ref")" = true ]; then
    current_was_paused=true
  else
    current_was_paused=false
    docker pause "$container_ref" >/dev/null
  fi

  for state_path in "${present_paths[@]}"; do
    case "$state_path" in
      /data/openclaw) key=data-openclaw ;;
      /home/node/.openclaw) key=home-openclaw ;;
      /app/config) key=app-config ;;
      /app/skills) key=app-skills ;;
      /app/plugins) key=app-plugins ;;
      /opt/data) key=opt-data ;;
      *) echo "unexpected state path" >&2; return 1 ;;
    esac
    install -d -m 0770 "$destination/state/$key"
    docker cp "$container_ref:$state_path/." "$destination/state/$key/"
    printf '%s|%s\n' "$key" "$state_path" >> "$destination/state-paths.tsv"
  done

  if [ "$current_was_paused" = false ]; then
    docker unpause "$container_ref" >/dev/null
  fi
  current_container=""
  current_was_paused=false
}

while IFS='|' read -r employee_b64 status lifecycle agent_type container_ref; do
  [ -n "$employee_b64" ] || continue
  employee_id="$(printf '%s' "$employee_b64" | base64 -d)"
  if ! printf '%s' "$employee_id" | grep -Eq '^[A-Za-z0-9@._+-]+$'; then
    echo "employee id contains an unsafe path character" >&2
    exit 1
  fi
  item_id="$(printf '%s' "$employee_id" | sha256sum | cut -c1-16)"
  item_dir="$staging_dir/items/$item_id"
  install -d -m 0770 "$item_dir"
  printf 'employee_id_base64=%s\nstatus=%s\nlifecycle=%s\nagent_type=%s\n' \
    "$employee_b64" "$status" "$lifecycle" "$agent_type" > "$item_dir/row.meta"

  host_source="$(realpath -m "$INSTANCE_ROOT/$employee_id")"
  case "$host_source" in
    "$(realpath -m "$INSTANCE_ROOT")"/*)
      if [ -d "$host_source" ]; then
        install -d -m 0770 "$item_dir/host-instance"
        cp -a -- "$host_source/." "$item_dir/host-instance/"
      fi
      ;;
    *) echo "resolved instance path escaped the instance root" >&2; exit 1 ;;
  esac

  if [ "$status" = running ]; then
    docker inspect --format \
      'id={{.Id}}{{println}}name={{.Name}}{{println}}image={{.Config.Image}}{{println}}state={{.State.Status}}' \
      "$container_ref" > "$item_dir/container.meta"
    copy_container_state "$container_ref" "$item_dir"
  fi
done < "$rows_file"

printf 'session_id=%s\nfleet_rows=%s\nrunning_hermes=%s\nnonrunning=%s\n' \
  "$SESSION_ID" "$row_count" "$running_count" "$nonrunning_count" \
  > "$staging_dir/MANIFEST"
printf 'complete\n' > "$staging_dir/COMPLETE"

(
  cd "$staging_dir"
  find . -type f ! -name SHA256SUMS -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS
  sha256sum --check --quiet SHA256SUMS
)

mv -- "$staging_dir" "$final_dir"
setfacl -m u:shiyao:rwx,m::rwx,d:u:shiyao:rwx,d:m::rwx "$final_dir"
getfacl -cp "$final_dir" | grep -qx 'user:shiyao:rwx'
getfacl -cp "$final_dir" | grep -qx 'mask::rwx'
getfacl -cp "$final_dir" | grep -qx 'default:user:shiyao:rwx'
getfacl -cp "$final_dir" | grep -qx 'default:mask::rwx'
printf 'backup_complete=%s\n' "$final_dir"
