#!/usr/bin/env bash
set -Eeuo pipefail

self_test_dir=''

usage() {
  echo "usage: $0 acquire|verify|release LOCK_PATH STATE_PATH OWNER_TOKEN" >&2
  echo "       $0 recover LOCK_PATH STATE_PATH OWNER_TOKEN --operator-approved-recovery" >&2
  echo "       $0 --self-test" >&2
}

die() {
  echo "mypc release lock: $*" >&2
  exit 1
}

require_runtime_tools() {
  command -v flock >/dev/null 2>&1 || die "Linux flock is required"
  [ -r /proc/self/stat ] || die "Linux /proc process identity is required"
}

require_arguments() {
  case "${1:-}" in
    recover)
      [ "$#" -eq 5 ] && [ "$5" = --operator-approved-recovery ] || {
        die "recover requires the independent --operator-approved-recovery approval"
      }
      ;;
    acquire|verify|release)
      [ "$#" -eq 4 ] || { usage; exit 2; }
      ;;
    *) usage; exit 2 ;;
  esac
  case "$2:$3" in
    /*:/*) ;;
    *) die "lock and state paths must be absolute" ;;
  esac
  [[ "$4" =~ ^[A-Za-z0-9._:-]{8,128}$ ]] || die "invalid owner token"
  [ -d "$(dirname -- "$2")" ] || die "lock directory does not exist"
  [ -d "$(dirname -- "$3")" ] || die "state directory does not exist"
}

state_value() {
  local key="$1"
  local state_path="$2"
  awk -F= -v expected="$key" '$1 == expected { print substr($0, index($0, "=") + 1); exit }' "$state_path"
}

process_start_token() {
  local pid="$1"
  [ -r "/proc/$pid/stat" ] || return 1
  awk '{print $22}' "/proc/$pid/stat"
}

process_is_running() {
  local pid="$1"
  local process_state
  kill -0 "$pid" 2>/dev/null || return 1
  if [ -r "/proc/$pid/stat" ]; then
    process_state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)" || return 1
    [ -n "$process_state" ] && [ "$process_state" != Z ] || return 1
  fi
  return 0
}

process_identity_is_running() {
  local pid="$1"
  local expected_start="$2"
  local actual_start
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ "$expected_start" =~ ^[0-9]+$ ]] || return 1
  process_is_running "$pid" || return 1
  actual_start="$(process_start_token "$pid" 2>/dev/null || true)"
  [ -n "$actual_start" ] && [ "$actual_start" = "$expected_start" ]
}

holder_code() {
  cat <<'HOLDER'
set -Eeuo pipefail
lock_path=$1
state_path=$2
owner_token=$3
process_start_token() {
  local pid="$1"
  [ -r "/proc/$pid/stat" ] || return 1
  awk '{print $22}' "/proc/$pid/stat"
}
write_state() {
  local temp_path="$state_path.$$"
  local current_owner
  current_owner="$(awk -F= '$1 == "owner_token" { print substr($0, index($0, "=") + 1); exit }' "$state_path" 2>/dev/null || true)"
  [ "$current_owner" = "$owner_token" ] || exit 75
  printf 'owner_token=%s\nholder_pid=%s\nholder_start=%s\n' \
    "$owner_token" "$$" "$holder_start" >"$temp_path"
  mv -f -- "$temp_path" "$state_path"
}
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  flock -u 9 2>/dev/null || true
  exit "$status"
}
exec 9>"$lock_path"
flock -n 9 || exit 75
trap cleanup EXIT INT TERM
holder_start="$(process_start_token "$$")" || exit 75
write_state
while :; do
  sleep 60
done
HOLDER
}

signal_holder() {
  local signal="$1"
  local holder_pid="$2"
  [[ "$holder_pid" =~ ^[0-9]+$ ]] || return 1
  kill "-$signal" -- "-$holder_pid" 2>/dev/null || kill "-$signal" "$holder_pid"
}

lock_is_held() {
  local lock_path="$1"
  exec 8>"$lock_path"
  if flock -n 8; then
    flock -u 8
    exec 8>&-
    return 1
  fi
  exec 8>&-
  return 0
}

wait_for_exit() {
  local holder_pid="$1"
  local holder_start="$2"
  for _ in $(seq 1 100); do
    process_identity_is_running "$holder_pid" "$holder_start" || return 0
    sleep 0.1
  done
  if process_identity_is_running "$holder_pid" "$holder_start"; then
    signal_holder KILL "$holder_pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 100); do
    process_identity_is_running "$holder_pid" "$holder_start" || return 0
    sleep 0.1
  done
  return 1
}

holder_matches_owner() {
  local holder_pid="$1"
  local owner_token="$2"
  local holder_start="$3"
  local command_line
  process_identity_is_running "$holder_pid" "$holder_start" || return 1
  [ -r "/proc/$holder_pid/cmdline" ] || return 1
  command_line="$(tr '\0' ' ' <"/proc/$holder_pid/cmdline")"
  case "$command_line" in
    *"$owner_token"*) return 0 ;;
    *) return 1 ;;
  esac
}

terminate_verified_holder() {
  local holder_pid="$1"
  local holder_start="$2"
  local owner_token="$3"
  process_identity_is_running "$holder_pid" "$holder_start" || return 0
  holder_matches_owner "$holder_pid" "$owner_token" "$holder_start" \
    || return 1
  signal_holder TERM "$holder_pid" || return 1
  wait_for_exit "$holder_pid" "$holder_start"
}

acquire_lock() {
  local lock_path="$1"
  local state_path="$2"
  local owner_token="$3"
  local holder_code_text
  local launcher_pid=''
  local state_holder_pid
  local state_holder_start
  local lock_ready=false
  local state_claimed=false
  local current_owner

  acquire_cleanup() {
    local status=$?
    local cleanup_holder_pid=''
    local cleanup_holder_start=''
    local cleanup_safe=false
    trap - EXIT INT TERM
    if [ "$lock_ready" != true ] && [ "$state_claimed" = true ] && [ -e "$state_path" ]; then
      current_owner="$(state_value owner_token "$state_path" 2>/dev/null || true)"
      if [ "$current_owner" != "$owner_token" ]; then
        echo "acquire cleanup preserved foreign state after a state collision" >&2
        exit "$status"
      fi
      cleanup_holder_pid="$(state_value holder_pid "$state_path" 2>/dev/null || true)"
      cleanup_holder_start="$(state_value holder_start "$state_path" 2>/dev/null || true)"
      if [ "$cleanup_holder_pid" = 0 ] && [ "$cleanup_holder_start" = pending ]; then
        cleanup_safe=true
      elif [[ "$cleanup_holder_pid" =~ ^[0-9]+$ ]] && [[ "$cleanup_holder_start" =~ ^[0-9]+$ ]]; then
        if process_identity_is_running "$cleanup_holder_pid" "$cleanup_holder_start"; then
          if ! terminate_verified_holder "$cleanup_holder_pid" "$cleanup_holder_start" "$owner_token"; then
            echo "acquire cleanup could not verify its holder identity; state preserved" >&2
            exit "$status"
          fi
          cleanup_safe=true
        elif ! lock_is_held "$lock_path"; then
          cleanup_safe=true
        fi
      elif [ -n "$cleanup_holder_pid" ] || [ -n "$cleanup_holder_start" ]; then
        echo "acquire cleanup found an unverifiable holder identity; state preserved" >&2
        exit "$status"
      else
        cleanup_safe=true
      fi
      if [ "$cleanup_safe" = true ]; then
        rm -f -- "$state_path"
      fi
    fi
    exit "$status"
  }
  trap acquire_cleanup EXIT INT TERM

  umask 077
  if ! (set -o noclobber; printf 'owner_token=%s\nholder_pid=0\nholder_start=pending\n' \
    "$owner_token" >"$state_path") 2>/dev/null; then
    die "state already exists; use operator-gated recover with its owner token"
  fi
  state_claimed=true

  # Test-only pause lets the Linux self-check cancel a reservation before a
  # holder exists; normal workflow invocations never set this variable.
  if [ -n "${VECTA_RELEASE_LOCK_STARTUP_PAUSE_FILE:-}" ]; then
    : >"$VECTA_RELEASE_LOCK_STARTUP_PAUSE_FILE"
    while [ -e "$VECTA_RELEASE_LOCK_STARTUP_PAUSE_FILE" ]; do
      sleep 0.01
    done
  fi

  holder_code_text="$(holder_code)"
  setsid /bin/bash -c "$holder_code_text" _ "$lock_path" "$state_path" \
    "$owner_token" </dev/null >/dev/null 2>&1 &
  launcher_pid=$!

  for _ in $(seq 1 50); do
    if [ -s "$state_path" ]; then
      current_owner="$(state_value owner_token "$state_path" 2>/dev/null || true)"
      [ "$current_owner" = "$owner_token" ] || die "release lock state collision; foreign state preserved"
      state_holder_pid="$(state_value holder_pid "$state_path" 2>/dev/null || true)"
      state_holder_start="$(state_value holder_start "$state_path" 2>/dev/null || true)"
      if [[ "$state_holder_pid" =~ ^[0-9]+$ ]] \
        && [[ "$state_holder_start" =~ ^[0-9]+$ ]] \
        && [ "$state_holder_pid" != 0 ] \
        && process_identity_is_running "$state_holder_pid" "$state_holder_start" \
        && holder_matches_owner "$state_holder_pid" "$owner_token" "$state_holder_start" \
        && lock_is_held "$lock_path"; then
        lock_ready=true
        trap - EXIT INT TERM
        exit 0
      fi
    fi
    if [ -n "$launcher_pid" ] && ! process_is_running "$launcher_pid" \
      && [ "$(state_value holder_pid "$state_path" 2>/dev/null || true)" = 0 ]; then
      die "lock holder failed to start"
    fi
    sleep 0.1
  done
  die "lock holder did not become ready; owner-gated cleanup will preserve foreign state"
}

verify_lock() {
  local lock_path="$1"
  local state_path="$2"
  local owner_token="$3"
  local state_owner
  local holder_pid
  local holder_start
  [ -s "$state_path" ] || die "no release-lock state exists"
  state_owner="$(state_value owner_token "$state_path")"
  holder_pid="$(state_value holder_pid "$state_path")"
  holder_start="$(state_value holder_start "$state_path")"
  [ "$state_owner" = "$owner_token" ] || die "release lease owner does not match lock state"
  [[ "$holder_pid" =~ ^[1-9][0-9]*$ ]] || die "release lease has no holder identity"
  [[ "$holder_start" =~ ^[0-9]+$ ]] || die "release lease has no holder start identity"
  holder_matches_owner "$holder_pid" "$owner_token" "$holder_start" \
    || die "release lease holder identity cannot be verified"
  lock_is_held "$lock_path" || die "release lease is not held"
}

terminal_release_lock() {
  local lock_path="$1"
  local state_path="$2"
  local owner_token="$3"
  local state_owner
  local holder_pid
  local holder_start

  [ -s "$state_path" ] || die "no release-lock state exists"
  state_owner="$(state_value owner_token "$state_path")"
  holder_pid="$(state_value holder_pid "$state_path")"
  holder_start="$(state_value holder_start "$state_path")"
  [ "$state_owner" = "$owner_token" ] || die "owner token does not match lock state"
  if [ "$holder_pid" = 0 ] && [ "$holder_start" = pending ]; then
    die "lock acquisition is still pending; use operator-gated recover"
  fi
  [[ "$holder_pid" =~ ^[1-9][0-9]*$ ]] || die "lock state has an invalid holder pid"
  [[ "$holder_start" =~ ^[0-9]+$ ]] || die "lock state has no holder identity"

  if process_identity_is_running "$holder_pid" "$holder_start"; then
    terminate_verified_holder "$holder_pid" "$holder_start" "$owner_token" \
      || die "holder identity could not be verified or terminated"
  fi

  exec 8>"$lock_path"
  flock -n 8 || die "lock holder still owns the release lock"
  # Keep the kernel lock until state deletion so a new owner cannot reserve
  # state between the release proof and rm.
  state_owner="$(state_value owner_token "$state_path" 2>/dev/null || true)"
  [ "$state_owner" = "$owner_token" ] || die "lock state changed during release"
  holder_pid="$(state_value holder_pid "$state_path" 2>/dev/null || true)"
  holder_start="$(state_value holder_start "$state_path" 2>/dev/null || true)"
  if [[ "$holder_pid" =~ ^[1-9][0-9]*$ ]] \
    && [[ "$holder_start" =~ ^[0-9]+$ ]] \
    && process_identity_is_running "$holder_pid" "$holder_start"; then
    die "lock holder still owns the release lock"
  fi
  rm -f -- "$state_path"
  flock -u 8
  exec 8>&-
}

recover_stale_lock() {
  local lock_path="$1"
  local state_path="$2"
  local owner_token="$3"
  local recovery_approved="$4"
  local state_owner
  local holder_pid
  local holder_start
  local observed_owner
  local observed_pid
  local observed_start

  [ "$recovery_approved" = true ] \
    || die "recover requires independent operator approval"
  [ -s "$state_path" ] || die "no release-lock state exists"
  state_owner="$(state_value owner_token "$state_path")"
  holder_pid="$(state_value holder_pid "$state_path")"
  holder_start="$(state_value holder_start "$state_path")"
  [ "$state_owner" = "$owner_token" ] \
    || die "owner token does not match lock state; foreign state preserved"

  [[ "$holder_pid" =~ ^[1-9][0-9]*$ ]] \
    || die "stale recovery requires a verifiable holder pid"
  [[ "$holder_start" =~ ^[0-9]+$ ]] \
    || die "stale recovery requires a verifiable holder start identity"
  if process_identity_is_running "$holder_pid" "$holder_start"; then
    die "live holder is not recoverable; use terminal release"
  fi

  # Recovery never signals a process. Re-acquiring the kernel lock proves that
  # the recorded holder is stale before the state file can be removed.
  exec 8>"$lock_path"
  if ! flock -n 8; then
    exec 8>&-
    die "release lock is still held; live recovery is forbidden"
  fi
  observed_owner="$(state_value owner_token "$state_path" 2>/dev/null || true)"
  observed_pid="$(state_value holder_pid "$state_path" 2>/dev/null || true)"
  observed_start="$(state_value holder_start "$state_path" 2>/dev/null || true)"
  if [ "$observed_owner" != "$state_owner" ] \
    || [ "$observed_pid" != "$holder_pid" ] \
    || [ "$observed_start" != "$holder_start" ]; then
    flock -u 8
    exec 8>&-
    die "lock state changed during recovery; state preserved"
  fi
  if [[ "$observed_pid" =~ ^[1-9][0-9]*$ ]] \
    && [[ "$observed_start" =~ ^[0-9]+$ ]] \
    && process_identity_is_running "$observed_pid" "$observed_start"; then
    flock -u 8
    exec 8>&-
    die "live holder appeared during recovery; state preserved"
  fi
  rm -f -- "$state_path"
  flock -u 8
  exec 8>&-
}

release_lock() {
  local lock_path="$1"
  local state_path="$2"
  local owner_token="$3"
  local action="$4"
  local recovery_approved="$5"
  case "$action" in
    release) terminal_release_lock "$lock_path" "$state_path" "$owner_token" ;;
    recover) recover_stale_lock "$lock_path" "$state_path" "$owner_token" "$recovery_approved" ;;
    *) die "unsupported lease action: $action" ;;
  esac
}

self_test() {
  local token=0123456789abcdef
  local second_token=fedcba9876543210
  local holder_pid
  local holder_start
  local race_dir
  local race_pid_a
  local race_pid_b
  local race_winner_token
  local startup_pid
  local startup_gate
  local held_lock_pid
  local held_lock_gate
  self_test_dir="$(mktemp -d "/tmp/mypc-release-lock.XXXXXX")"
  cleanup_self_test() {
    local status=$?
    local cleanup_failed=false
    local cleanup_lock
    local cleanup_state
    local cleanup_owner
    local cleanup_pid
    local cleanup_start
    local child_pid
    set +e
    set +u
    trap - EXIT INT TERM

    stop_known_child() {
      child_pid="$1"
      if [ -n "$child_pid" ]; then
        if process_is_running "$child_pid"; then
          signal_holder TERM "$child_pid" 2>/dev/null || true
        fi
        wait "$child_pid" 2>/dev/null || true
        if process_is_running "$child_pid"; then
          signal_holder KILL "$child_pid" 2>/dev/null || true
          wait "$child_pid" 2>/dev/null || true
        fi
      fi
    }

    cleanup_lease() {
      cleanup_lock="$1"
      cleanup_state="$2"
      cleanup_owner=''
      if [ ! -d "$(dirname -- "$cleanup_lock")" ]; then
        [ ! -e "$cleanup_state" ] || cleanup_failed=true
        return
      fi
      if [ -s "$cleanup_state" ]; then
        cleanup_owner="$(state_value owner_token "$cleanup_state" 2>/dev/null || true)"
        case "$cleanup_owner" in
          "$token"|"$second_token")
            cleanup_pid="$(state_value holder_pid "$cleanup_state" 2>/dev/null || true)"
            cleanup_start="$(state_value holder_start "$cleanup_state" 2>/dev/null || true)"
            if [ "$cleanup_pid" = 0 ] && [ "$cleanup_start" = pending ]; then
              if ! lock_is_held "$cleanup_lock"; then
                rm -f -- "$cleanup_state"
              fi
            else
              "$0" release "$cleanup_lock" "$cleanup_state" "$cleanup_owner" \
                >/dev/null 2>&1 || cleanup_failed=true
            fi
            ;;
          *) cleanup_failed=true ;;
        esac
      fi
      if [ -e "$cleanup_state" ] || lock_is_held "$cleanup_lock"; then
        cleanup_failed=true
      fi
    }

    stop_known_child "${startup_pid:-}"
    startup_pid=''
    stop_known_child "${race_pid_a:-}"
    race_pid_a=''
    stop_known_child "${race_pid_b:-}"
    race_pid_b=''
    stop_known_child "${held_lock_pid:-}"
    held_lock_pid=''
    rm -f -- "${self_test_dir:-}/startup-gate" "${self_test_dir:-}/race/go" \
      "${self_test_dir:-}/held-lock-ready"
    cleanup_lease "$self_test_dir/lock" "$self_test_dir/state"
    cleanup_lease "$self_test_dir/race/lock" "$self_test_dir/race/state"
    cleanup_lease "$self_test_dir/startup-lock" "$self_test_dir/startup-state"
    cleanup_lease "$self_test_dir/dead-lock" "$self_test_dir/dead-state"
    cleanup_lease "$self_test_dir/held-lock" "$self_test_dir/held-state"
    if [ "$cleanup_failed" = true ]; then
      echo "self-test cleanup could not prove every holder stopped and flock released" >&2
      exit 1
    fi
    rm -rf -- "${self_test_dir:-}"
    self_test_dir=''
    exit "$status"
  }
  trap cleanup_self_test EXIT

  "$0" acquire "$self_test_dir/lock" "$self_test_dir/state" "$token"
  "$0" verify "$self_test_dir/lock" "$self_test_dir/state" "$token"
  test "$(state_value owner_token "$self_test_dir/state")" = "$token"
  holder_pid="$(state_value holder_pid "$self_test_dir/state")"
  holder_start="$(state_value holder_start "$self_test_dir/state")"
  process_identity_is_running "$holder_pid" "$holder_start" \
    || die "self-test lost the live holder before recovery"
  cp -- "$self_test_dir/state" "$self_test_dir/state-before-foreign"
  # Simulate separate deploy and health consumers: the lease remains held
  # while the next consumer verifies it, so no operator can enter the gap.
  "$0" verify "$self_test_dir/lock" "$self_test_dir/state" "$token"
  if "$0" acquire "$self_test_dir/lock" "$self_test_dir/state-2" "$second_token"; then
    die "self-test allowed a concurrent claim after ready"
  fi
  cmp -s "$self_test_dir/state-before-foreign" "$self_test_dir/state" \
    || die "failed concurrent claim changed the winner state"
  "$0" verify "$self_test_dir/lock" "$self_test_dir/state" "$token"
  if "$0" acquire "$self_test_dir/lock" "$self_test_dir/state" "$second_token"; then
    die "self-test allowed a conflicting state claim"
  fi
  cmp -s "$self_test_dir/state-before-foreign" "$self_test_dir/state" \
    || die "same-state collision changed the winner state"
  if "$0" recover "$self_test_dir/lock" "$self_test_dir/state" "$second_token"; then
    die "self-test allowed recovery without independent approval"
  fi
  cmp -s "$self_test_dir/state-before-foreign" "$self_test_dir/state" \
    || die "foreign recovery changed the winner state"
  if "$0" recover "$self_test_dir/lock" "$self_test_dir/state" "$second_token" \
    --operator-approved-recovery; then
    die "self-test allowed recovery with the wrong owner token"
  fi
  cmp -s "$self_test_dir/state-before-foreign" "$self_test_dir/state" \
    || die "foreign recovery changed the winner state"
  if "$0" recover "$self_test_dir/lock" "$self_test_dir/state" "$token" \
    --operator-approved-recovery; then
    die "self-test allowed recovery of a live holder"
  fi
  cmp -s "$self_test_dir/state-before-foreign" "$self_test_dir/state" \
    || die "live recovery changed the winner state"
  lock_is_held "$self_test_dir/lock" \
    || die "live recovery released the winner lock"
  process_identity_is_running "$holder_pid" "$holder_start" \
    || die "live recovery changed the holder"
  "$0" release "$self_test_dir/lock" "$self_test_dir/state" "$token"
  test ! -e "$self_test_dir/state"

  # Both claimants cross the initial no-state boundary together. O_EXCL
  # reservation makes exactly one winner and prevents the loser cleanup from
  # touching its live state or holder.
  race_dir="$self_test_dir/race"
  mkdir -p "$race_dir"
  mkfifo "$race_dir/go"
  (
    : >"$race_dir/ready-a"
    exec 7<"$race_dir/go"
    IFS= read -r -n 1 <&7
    if "$0" acquire "$race_dir/lock" "$race_dir/state" "$token"; then
      echo success >"$race_dir/result-a"
      sleep 2
      "$0" release "$race_dir/lock" "$race_dir/state" "$token"
    else
      echo failure >"$race_dir/result-a"
    fi
  ) &
  race_pid_a=$!
  (
    : >"$race_dir/ready-b"
    exec 7<"$race_dir/go"
    IFS= read -r -n 1 <&7
    if "$0" acquire "$race_dir/lock" "$race_dir/state" "$second_token"; then
      echo success >"$race_dir/result-b"
      sleep 2
      "$0" release "$race_dir/lock" "$race_dir/state" "$second_token"
    else
      echo failure >"$race_dir/result-b"
    fi
  ) &
  race_pid_b=$!
  for _ in $(seq 1 100); do
    [ -e "$race_dir/ready-a" ] && [ -e "$race_dir/ready-b" ] && break
    sleep 0.01
  done
  test -e "$race_dir/ready-a" && test -e "$race_dir/ready-b"
  exec 8>"$race_dir/go"
  printf 'ab' >&8
  exec 8>&-
  for _ in $(seq 1 100); do
    [ -e "$race_dir/result-a" ] && [ -e "$race_dir/result-b" ] && break
    sleep 0.01
  done
  test -e "$race_dir/result-a" && test -e "$race_dir/result-b"
  if [ "$(cat "$race_dir/result-a")" = success ]; then
    test "$(cat "$race_dir/result-b")" = failure
    race_winner_token="$token"
  else
    test "$(cat "$race_dir/result-a")" = failure
    test "$(cat "$race_dir/result-b")" = success
    race_winner_token="$second_token"
  fi
  test -s "$race_dir/state"
  test "$(state_value owner_token "$race_dir/state")" = "$race_winner_token"
  holder_pid="$(state_value holder_pid "$race_dir/state")"
  holder_start="$(state_value holder_start "$race_dir/state")"
  process_identity_is_running "$holder_pid" "$holder_start" \
    || die "synchronized loser claim killed the winner holder"
  lock_is_held "$race_dir/lock" \
    || die "synchronized loser claim released the winner lock"
  cp -- "$race_dir/state" "$race_dir/state-before-loser-exit"
  sleep 0.1
  cmp -s "$race_dir/state-before-loser-exit" "$race_dir/state" \
    || die "synchronized loser claim changed the winner state"
  wait "$race_pid_a" 2>/dev/null || true
  wait "$race_pid_b" 2>/dev/null || true
  test ! -e "$race_dir/state"

  # Cancel while the atomic reservation is still pending; no holder exists,
  # so cleanup may remove only this owner's reservation.
  startup_gate="$self_test_dir/startup-gate"
  : >"$startup_gate"
  (
    VECTA_RELEASE_LOCK_STARTUP_PAUSE_FILE="$startup_gate" \
      "$0" acquire "$self_test_dir/startup-lock" "$self_test_dir/startup-state" "$token"
  ) &
  startup_pid=$!
  for _ in $(seq 1 100); do
    if [ -s "$self_test_dir/startup-state" ] \
      && [ "$(state_value holder_pid "$self_test_dir/startup-state")" = 0 ]; then
      break
    fi
    sleep 0.01
  done
  test -s "$self_test_dir/startup-state"
  test "$(state_value holder_start "$self_test_dir/startup-state")" = pending
  kill -TERM "$startup_pid" 2>/dev/null || true
  wait "$startup_pid" 2>/dev/null || true
  rm -f -- "$startup_gate"
  test ! -e "$self_test_dir/startup-state"
  ! lock_is_held "$self_test_dir/startup-lock"

  # A non-pending state whose recorded process identity is dead is recoverable
  # only after the operator approval and a successful kernel-lock proof.
  printf 'owner_token=%s\nholder_pid=999999999\nholder_start=1\n' "$token" \
    >"$self_test_dir/dead-state"
  "$0" recover "$self_test_dir/dead-lock" "$self_test_dir/dead-state" "$token" \
    --operator-approved-recovery
  test ! -e "$self_test_dir/dead-state"
  ! lock_is_held "$self_test_dir/dead-lock"

  # A dead recorded PID is still not recoverable while another process holds
  # the kernel lock. Recovery must preserve both the stale state and holder.
  held_lock_gate="$self_test_dir/held-lock-ready"
  (
    exec 9>"$self_test_dir/held-lock"
    flock -n 9
    : >"$held_lock_gate"
    sleep 2
  ) &
  held_lock_pid=$!
  for _ in $(seq 1 100); do
    [ -e "$held_lock_gate" ] && break
    sleep 0.01
  done
  test -e "$held_lock_gate"
  printf 'owner_token=%s\nholder_pid=999999999\nholder_start=1\n' "$token" \
    >"$self_test_dir/held-state"
  cp -- "$self_test_dir/held-state" "$self_test_dir/held-state-before-recovery"
  if "$0" recover "$self_test_dir/held-lock" "$self_test_dir/held-state" "$token" \
    --operator-approved-recovery; then
    die "self-test recovered a state while the kernel lock was held"
  fi
  cmp -s "$self_test_dir/held-state-before-recovery" "$self_test_dir/held-state" \
    || die "held-lock recovery changed stale state"
  lock_is_held "$self_test_dir/held-lock" \
    || die "held-lock recovery released a foreign holder lock"
  process_is_running "$held_lock_pid" \
    || die "held-lock recovery terminated the foreign holder"
  wait "$held_lock_pid"
  held_lock_pid=''
  rm -f -- "$held_lock_gate"
  "$0" recover "$self_test_dir/held-lock" "$self_test_dir/held-state" "$token" \
    --operator-approved-recovery
  test ! -e "$self_test_dir/held-state"
  ! lock_is_held "$self_test_dir/held-lock"

  "$0" acquire "$self_test_dir/lock" "$self_test_dir/state" "$second_token"
  "$0" release "$self_test_dir/lock" "$self_test_dir/state" "$second_token"
  echo "mypc release lock self-check passed"
}

if [ "$#" -ge 1 ] && [ "$1" = "--self-test" ]; then
  [ "$#" -eq 1 ] || { usage; exit 2; }
  require_runtime_tools
  self_test
  exit 0
fi

require_arguments "$@"
require_runtime_tools
command="$1"
lock_path="$2"
state_path="$3"
owner_token="$4"
recovery_approved=false
if [ "$command" = recover ]; then
  [ "$#" -eq 5 ] && [ "$5" = --operator-approved-recovery ] || {
    usage
    exit 2
  }
  recovery_approved=true
fi
case "$command" in
  acquire) acquire_lock "$lock_path" "$state_path" "$owner_token" ;;
  verify) verify_lock "$lock_path" "$state_path" "$owner_token" ;;
  release|recover) release_lock "$lock_path" "$state_path" "$owner_token" "$command" "$recovery_approved" ;;
esac
