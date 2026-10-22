"""Safety regression tests for the separately approved Contabo release workflow.

These tests are offline and never connect to the production server.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ops" / "contabo_pilot_release.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "contabo-pilot-release.yml"


def test_release_script_is_valid_bash_and_requires_two_independent_approvals() -> None:
    syntax = subprocess.run(["/bin/bash", "-n", str(SCRIPT)], check=False, capture_output=True)  # noqa: S603
    assert syntax.returncode == 0, syntax.stderr
    text = SCRIPT.read_text()
    assert 'FM_DEPLOY_APPROVE_MIGRATIONS:-}' in text
    assert 'FM_DEPLOY_APPROVE_RELEASE:-}' in text
    assert "rls_invariant.sql" in text
    assert "pg_restore --exit-on-error" in text
    assert "SOURCE_COMMIT" in text
    assert "docker compose down" not in text
    assert "docker volume rm" not in text


def test_release_fails_closed_without_any_credentials_or_approval() -> None:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    res = subprocess.run(  # noqa: S603 (fixed repository script)
        ["/bin/bash", str(SCRIPT), "deploy"],
        capture_output=True, text=True, env=env, check=False, timeout=5,
    )
    assert res.returncode != 0
    assert "Missing approved SHA" in res.stderr
    assert "DEPLOY_OK" not in res.stdout


def test_workflow_requires_main_exact_sha_manual_trigger_and_private_repo_safe_transport() -> None:
    text = WORKFLOW.read_text()
    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    assert "push:" not in text
    assert "environment: contabo-pilot" in text
    assert 'refs/heads/main' in text
    assert "git rev-parse HEAD" in text
    assert "CONTABO_DEPLOY_ENABLED" in text
    assert "CONTABO_SSH_KNOWN_HOSTS" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "github.actor" not in text or "GITHUB_ACTOR" in text
    assert "github.repository" in text
    assert "git archive --format=tar HEAD" in text
    assert "approve_migrations" in text and "approve_deploy" in text
    assert "api web e2e" in text
    assert "deploy --auto" not in text
