# This library is loaded only by the temporary driver for a verified scoped dispatch.
scoped_require_anchor() {
  [[ "$N" == "$SCOPED_ISSUE" && "$PR_NUM" == "$SCOPED_PR" && "$MERGE_SHA" == "$SCOPED_UPSTREAM" ]] || {
    echo 'release ticket, merged pull request, or upstream SHA differs from the verified manifest' >&2
    return 1
  }
}

# Only the approved ticket can claim a patch that was backported onto a different parent.
scoped_ticket_deployed() {
  local ticket="$1" merge="$2" deployed="$3"
  [[ "$ticket" == "$SCOPED_ISSUE" && "$merge" == "$SCOPED_UPSTREAM" && "$deployed" == "$SCOPED_SOURCE" ]]
}

scoped_require_plan() {
  local svc
  [[ ${#RELEASE_IMAGE_DRIFT_BLOCKED_SERVICES[@]} -eq 0 && ${#FLEET_ROLL_PENDING_SERVICES[@]} -eq 0 ]] || {
    echo "scoped release cannot omit drift-blocked or pending services: ${RELEASE_IMAGE_DRIFT_BLOCKED_SERVICES[*]} ${FLEET_ROLL_PENDING_SERVICES[*]}" >&2
    return 1
  }
  local -a needed
  IFS=, read -r -a needed <<< "$SCOPED_SERVICES"
  [[ ${#needed[@]} -eq ${#RELEASE_TARGET_SERVICES[@]} ]] || return 1
  for svc in "${needed[@]}"; do
    [[ " ${RELEASE_TARGET_SERVICES[*]} " == *" $svc "* ]] || return 1
  done
}

scoped_require_readbacks() {
  local service
  local -a needed
  IFS=, read -r -a needed <<< "$SCOPED_SERVICES"
  for service in "${needed[@]}"; do
    [[ "${DEPLOYED_IMAGE_IDS[$service]:-}" =~ ^sha256:[a-f0-9]{64}$ &&
       "${DEPLOYED_IMAGE_REVISIONS[$service]:-}" == "$SCOPED_SOURCE" ]] || {
      echo "missing exact image ID/revision readback for $service" >&2
      return 1
    }
  done
}
