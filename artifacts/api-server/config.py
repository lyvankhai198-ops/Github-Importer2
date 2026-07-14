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

# ── Translation provider (see services/translation_service.py) ──
# "auto" tries LibreTranslate first, then the Anthropic LLM translator, then
# the deterministic regex/dictionary translator. LibreTranslate only
# activates once LIBRETRANSLATE_URL is set — otherwise it's skipped and the
# LLM fallback (already working today) handles everything unchanged.
TRANSLATION_PROVIDER = os.environ.get("TRANSLATION_PROVIDER", "auto")
LIBRETRANSLATE_URL = os.environ.get("LIBRETRANSLATE_URL", "")
LIBRETRANSLATE_API_KEY = os.environ.get("LIBRETRANSLATE_API_KEY", "")
TRANSLATION_TIMEOUT_SECONDS = int(os.environ.get("TRANSLATION_TIMEOUT_SECONDS", "10"))
