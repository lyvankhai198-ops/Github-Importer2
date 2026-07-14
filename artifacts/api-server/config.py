import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production-12345")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

DATABASE_URL = f"sqlite:///{BASE_DIR}/ai_center.db"

API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "30"))
API_MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", "3"))

UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

PORT = int(os.environ.get("PORT", "3000"))

# ── GitHub webhook auto-deploy (VPS only — see routers/github_webhook.py) ──
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
DEPLOY_SCRIPT_PATH = os.environ.get("DEPLOY_SCRIPT_PATH", "/root/deploy-aicenter.sh")
DEPLOY_BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
