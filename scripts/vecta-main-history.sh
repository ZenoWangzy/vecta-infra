#!/usr/bin/env bash

# A repository may have shallow grafts for refs unrelated to main. Release
# history is complete when no shallow boundary is reachable from the main ref.
vecta_main_history_is_complete() {
  local git_dir="$1"
  local main_ref="${2:-refs/heads/main}"
  local shallow_file boundary result

  git --git-dir="$git_dir" rev-parse --verify "$main_ref^{commit}" >/dev/null 2>&1 || return 1
  shallow_file="$(git --git-dir="$git_dir" rev-parse --git-path shallow 2>/dev/null)" || return 1
  [[ -f "$shallow_file" ]] || return 0

  while IFS= read -r boundary || [[ -n "$boundary" ]]; do
    [[ -n "$boundary" ]] || continue
    git --git-dir="$git_dir" cat-file -e "$boundary^{commit}" 2>/dev/null || return 1
    if git --git-dir="$git_dir" merge-base --is-ancestor "$boundary" "$main_ref" 2>/dev/null; then
      return 1
    else
      result=$?
      [[ "$result" -eq 1 ]] || return "$result"
    fi
  done < "$shallow_file"

  return 0
}
