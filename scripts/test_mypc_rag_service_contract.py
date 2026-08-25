#!/usr/bin/env python3
"""Executable contract for the mypc RAG environment overlay."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "inventories/mypc/hosts.ini"
GROUP_VARS = ROOT / "inventories/mypc/group_vars/mypc.yml"
PLAYBOOK = ROOT / "playbooks/mypc-rag-service.yml"
RAG_ROLE = ROOT / "roles/vecta-app/tasks/rag_service_mypc.yml"


def configured_hf_endpoint() -> str:
    inventory = GROUP_VARS.read_text()
    match = re.search(
        r"^rag_service_env_overrides:\n[ ]{2}HF_ENDPOINT:[ ]*([^\s#]+)",
        inventory,
        flags=re.MULTILINE,
    )
    assert match, "RAG Hugging Face endpoint must be versioned in mypc inventory"
    return match.group(1)


def main() -> None:
    for path in (INVENTORY, GROUP_VARS, PLAYBOOK, RAG_ROLE):
        assert path.exists(), f"missing RAG deployment contract: {path.name}"

    endpoint = configured_hf_endpoint()
    assert endpoint.startswith("https://"), (
        "RAG Hugging Face endpoint must be a non-empty HTTPS production contract"
    )

    role = RAG_ROLE.read_text()
    assert "combine(rag_service_env_overrides)" in role
    assert "Require a versioned RAG environment overlay" in role

    playbook = PLAYBOOK.read_text()
    assert "mypc_deploy_enabled" in playbook
    assert "Reject image upgrades in the RAG configuration-only path" in playbook
    assert "Capture the running RAG image reference" in playbook
    assert "rag_service_image: \"{{ mypc_rag_service_current_image.stdout | trim }}\"" in playbook
    assert "tasks_from: login.yml" in playbook
    assert "tasks_from: rag_service_mypc.yml" in playbook

    print("mypc RAG service contract: ok")


if __name__ == "__main__":
    main()
