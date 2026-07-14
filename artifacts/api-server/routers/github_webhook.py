"""
POST /github-webhook — secure GitHub webhook that triggers an auto-deploy
on the VPS when `main` is pushed. No cron/polling: GitHub calls this the
moment a push lands, we verify it, and hand off to /root/deploy-aicenter.sh
in the background.

GET /github-webhook/health — plain readiness check, never triggers a deploy.

This module intentionally does NOT implement git pull / pip install /
restart itself — that all lives in DEPLOY_SCRIPT_PATH on the VPS. This
endpoint only authenticates the request, filters to push-to-main, and runs
that script safely (no shell=True, bounded timeout, single-flight lock).
"""
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

import config

logger = logging.getLogger(__name__)
router = APIRouter()

DEPLOY_LOCK_FILE = "/tmp/aicenter-deploy.lock"
DEPLOY_LOG_FILE = os.environ.get("DEPLOY_LOG_FILE", "/var/log/aicenter-deploy.log")
DEPLOY_TIMEOUT_SECONDS = 300

# Redact anything that looks like a secret/token/password/key before it is
# ever written to the deploy log, even if the deploy script's own stdout
# happens to echo one back.
_SECRET_LINE_RE = re.compile(
    r"(?i)([A-Za-z0-9_]*(?:token|secret|password|api[_-]?key)[A-Za-z0-9_]*)\s*[:=]\s*\S+"
)


def _redact(text: str) -> str:
    return _SECRET_LINE_RE.sub(lambda m: f"{m.group(1)}=***REDACTED***", text)


def _verify_signature(secret: str, raw_body: bytes, signature_header: str) -> bool:
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _log_deploy(line: str) -> None:
    """Append one line to the deploy log. Falls back to the app logger if
    the configured log path isn't writable (e.g. a sandbox without
    /var/log access), so this can never crash the request or the
    background deploy task."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"[{ts}] {_redact(line)}"
    try:
        with open(DEPLOY_LOG_FILE, "a") as f:
            f.write(entry + "\n")
    except Exception:
        logger.info(f"[github-webhook] {entry}")


def _try_acquire_lock():
    """Non-blocking single-flight lock. Returns an open, locked file handle
    on success, or None if a deploy is already running."""
    try:
        fh = open(DEPLOY_LOCK_FILE, "w")
    except Exception as e:
        logger.error(f"[github-webhook] could not open lock file: {type(e).__name__}")
        return None
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    try:
        fh.write(str(os.getpid()))
        fh.flush()
    except Exception:
        pass
    return fh


def _release_lock(fh) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass
    try:
        os.remove(DEPLOY_LOCK_FILE)
    except Exception:
        pass


def _run_deploy(lock_fh, delivery_id: str, commit_hash: str, branch: str) -> None:
    """Runs in a background thread (FastAPI BackgroundTasks executes sync
    callables off the event loop). Holds `lock_fh` for the whole run so no
    second deploy can start concurrently, and always releases it."""
    start = time.monotonic()
    _log_deploy(
        f"delivery_id={delivery_id} commit={commit_hash} branch={branch} status=start"
    )
    try:
        script = config.DEPLOY_SCRIPT_PATH
        try:
            result = subprocess.run(
                [script],
                shell=False,
                capture_output=True,
                text=True,
                timeout=DEPLOY_TIMEOUT_SECONDS,
            )
            ok = result.returncode == 0
            _log_deploy(
                f"delivery_id={delivery_id} deploy_script_exit_code={result.returncode} "
                f"status={'success' if ok else 'failed'}"
            )
            for stream_name, content in (("stdout", result.stdout), ("stderr", result.stderr)):
                if content and content.strip():
                    tail = "\n".join(content.strip().splitlines()[-200:])
                    _log_deploy(f"delivery_id={delivery_id} {stream_name}:\n{tail}")
        except subprocess.TimeoutExpired:
            _log_deploy(
                f"delivery_id={delivery_id} status=failed reason=timeout_{DEPLOY_TIMEOUT_SECONDS}s"
            )
        except FileNotFoundError:
            _log_deploy(
                f"delivery_id={delivery_id} status=failed reason=deploy_script_not_found path={script}"
            )
        except Exception as e:
            _log_deploy(
                f"delivery_id={delivery_id} status=failed reason=exception error={type(e).__name__}"
            )
    finally:
        _release_lock(lock_fh)
        elapsed = time.monotonic() - start
        _log_deploy(f"delivery_id={delivery_id} status=done total_seconds={elapsed:.1f}")


@router.post("/github-webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_signature(config.GITHUB_WEBHOOK_SECRET, raw_body, signature):
        logger.warning("[github-webhook] rejected: missing/invalid signature")
        return JSONResponse(
            {"success": False, "message": "Invalid webhook signature"}, status_code=401
        )

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")

    if event != "push":
        # Covers "ping" (GitHub's initial webhook test) and any other event.
        return JSONResponse({"success": True, "message": "Ignored"}, status_code=200)

    try:
        payload = json.loads(raw_body)
    except Exception:
        return JSONResponse({"success": True, "message": "Ignored"}, status_code=200)

    ref = payload.get("ref", "")
    target_ref = f"refs/heads/{config.DEPLOY_BRANCH}"
    if ref != target_ref:
        return JSONResponse({"success": True, "message": "Ignored"}, status_code=200)

    commit_hash = str(payload.get("after") or "unknown")[:12]

    lock_fh = _try_acquire_lock()
    if lock_fh is None:
        return JSONResponse(
            {"success": True, "message": "Deployment already running"}, status_code=202
        )

    background_tasks.add_task(_run_deploy, lock_fh, delivery_id, commit_hash, config.DEPLOY_BRANCH)
    return JSONResponse({"success": True, "message": "Deployment started"}, status_code=202)


@router.get("/github-webhook/health")
async def github_webhook_health():
    return JSONResponse({"success": True, "service": "github-webhook", "status": "ready"})
