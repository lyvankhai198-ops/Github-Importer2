"""
Tests for the GitHub webhook auto-deploy endpoint:
  POST /github-webhook       — HMAC-signed, push-to-main only, backgrounds the deploy
  GET  /github-webhook/health — plain readiness check, never deploys

Uses a minimal FastAPI app wrapping only this router (avoids booting the
full app's bot/scheduler lifespan) and monkeypatches the lock/log file
paths to tmp_path so tests never touch the real /tmp or /var/log.
"""
import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from routers import github_webhook


SECRET = "test-webhook-secret"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(config, "DEPLOY_BRANCH", "main")
    monkeypatch.setattr(config, "DEPLOY_SCRIPT_PATH", str(tmp_path / "deploy.sh"))
    monkeypatch.setattr(github_webhook, "DEPLOY_LOCK_FILE", str(tmp_path / "deploy.lock"))
    monkeypatch.setattr(github_webhook, "DEPLOY_LOG_FILE", str(tmp_path / "deploy.log"))

    app = FastAPI()
    app.include_router(github_webhook.router)
    return TestClient(app)


def _push_payload(branch="main", after="abc123def456"):
    return json.dumps({"ref": f"refs/heads/{branch}", "after": after}).encode()


# ── signature verification ──────────────────────────────────────────────────

def test_missing_signature_returns_401(client):
    body = _push_payload()
    resp = client.post(
        "/github-webhook", content=body,
        headers={"X-GitHub-Event": "push"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"success": False, "message": "Invalid webhook signature"}


def test_wrong_signature_returns_401(client):
    body = _push_payload()
    resp = client.post(
        "/github-webhook", content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert resp.status_code == 401


# ── event/branch filtering ───────────────────────────────────────────────────

def test_ping_event_is_ignored(client):
    body = _push_payload()
    resp = client.post(
        "/github-webhook", content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(SECRET, body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "message": "Ignored"}


def test_push_to_other_branch_is_ignored(client):
    body = _push_payload(branch="develop")
    resp = client.post(
        "/github-webhook", content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _sign(SECRET, body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "message": "Ignored"}


# ── happy path + single-flight lock ─────────────────────────────────────────

def test_push_to_main_with_valid_signature_starts_deployment(client, tmp_path, monkeypatch):
    # Deploy script exists and exits 0 quickly so the background task
    # finishes almost immediately.
    script = tmp_path / "deploy.sh"
    script.write_text("#!/bin/sh\necho deployed\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setattr(config, "DEPLOY_SCRIPT_PATH", str(script))

    body = _push_payload(after="deadbeef1234")
    resp = client.post(
        "/github-webhook", content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _sign(SECRET, body),
            "X-GitHub-Delivery": "delivery-1",
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"success": True, "message": "Deployment started"}

    # Give the background task a moment to run and release the lock.
    for _ in range(20):
        if not (tmp_path / "deploy.lock").exists():
            break
        time.sleep(0.05)

    log_content = (tmp_path / "deploy.log").read_text()
    assert "delivery_id=delivery-1" in log_content
    assert "commit=deadbeef1234" in log_content
    assert "status=start" in log_content
    assert "status=success" in log_content or "deploy_script_exit_code=0" in log_content


def test_second_push_while_deploy_running_returns_already_running(client, tmp_path):
    # Simulate an in-flight deploy by holding the lock file open+locked
    # ourselves (same mechanism the handler uses).
    import fcntl
    lock_path = tmp_path / "deploy.lock"
    fh = open(lock_path, "w")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        body = _push_payload()
        resp = client.post(
            "/github-webhook", content=body,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _sign(SECRET, body),
            },
        )
        assert resp.status_code == 202
        assert resp.json() == {"success": True, "message": "Deployment already running"}
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# ── health endpoint ──────────────────────────────────────────────────────────

def test_health_endpoint_never_deploys(client):
    resp = client.get("/github-webhook/health")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "service": "github-webhook", "status": "ready"}


# ── security hygiene ─────────────────────────────────────────────────────────

def test_deploy_log_never_contains_the_webhook_secret(client, tmp_path, monkeypatch):
    script = tmp_path / "deploy.sh"
    # Deploy script output deliberately echoes something secret-shaped to
    # prove the log redacts it rather than leaking it verbatim.
    script.write_text(f"#!/bin/sh\necho 'TELEGRAM_TOKEN={SECRET}-leaked'\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setattr(config, "DEPLOY_SCRIPT_PATH", str(script))

    body = _push_payload()
    resp = client.post(
        "/github-webhook", content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _sign(SECRET, body),
        },
    )
    assert resp.status_code == 202

    for _ in range(20):
        if not (tmp_path / "deploy.lock").exists():
            break
        time.sleep(0.05)

    log_content = (tmp_path / "deploy.log").read_text()
    assert SECRET not in log_content
    assert "REDACTED" in log_content
