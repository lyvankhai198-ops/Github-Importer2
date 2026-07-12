import asyncio
import logging
from datetime import datetime
from typing import Optional
from database import SessionLocal
from models import TelegramBotConfig, BotStatus
from crypto import decrypt

logger = logging.getLogger(__name__)

# Reconnect backoff: 5s / 15s / 30s / 60s, cycling for up to 10 rounds
# (40 attempts), then falling back to a slow 5-minute retry indefinitely.
_BACKOFF_SEQUENCE = [5, 15, 30, 60]
_FAST_RETRY_ATTEMPTS = len(_BACKOFF_SEQUENCE) * 10
_SLOW_RETRY_SECONDS = 300


def _backoff_delay(attempt: int) -> int:
    if attempt <= _FAST_RETRY_ATTEMPTS:
        return _BACKOFF_SEQUENCE[(attempt - 1) % len(_BACKOFF_SEQUENCE)]
    return _SLOW_RETRY_SECONDS


class BotManager:
    _instance: Optional["BotManager"] = None

    def __init__(self):
        self._bot_task: Optional[asyncio.Task] = None
        self._application = None
        self._status = BotStatus.stopped
        self._bot_name = ""
        self._bot_username = ""
        self._stop_requested = False
        self._retry_count = 0
        self._last_error = ""

    @classmethod
    def get_instance(cls) -> "BotManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_running(self) -> bool:
        return self._bot_task is not None and not self._bot_task.done()

    def get_status(self) -> dict:
        return {
            "status": self._status.value if hasattr(self._status, "value") else str(self._status),
            "bot_name": self._bot_name,
            "bot_username": self._bot_username,
            "is_running": self.is_running(),
            "last_error": self._last_error,
            "retry_count": self._retry_count,
        }

    def _update_db_status(self, status: BotStatus, bot_name: str = None, bot_username: str = None):
        db = SessionLocal()
        try:
            cfg = db.query(TelegramBotConfig).first()
            if cfg:
                cfg.bot_status = status
                cfg.updated_at = datetime.utcnow()
                if bot_name is not None:
                    cfg.bot_name = bot_name
                if bot_username is not None:
                    cfg.bot_username = bot_username
                db.commit()
        except Exception as e:
            logger.error(f"DB status update error: {e}")
        finally:
            db.close()

    async def start_bot(self, token: str):
        """Start the bot under the watchdog supervisor. Idempotent — safe to
        call again while already running/reconnecting."""
        if self.is_running():
            logger.info("Bot already running")
            return
        self._stop_requested = False
        self._retry_count = 0
        self._last_error = ""
        self._status = BotStatus.starting
        self._update_db_status(BotStatus.starting)
        self._bot_task = asyncio.create_task(self._supervise(token))

    async def _supervise(self, token: str):
        """
        Watchdog loop: keeps the bot connected, reconnecting with backoff
        (5s/15s/30s/60s x10, then every 5 min) whenever it drops. Never lets
        a dropped connection kill the web app — only stop_bot() can end this.
        """
        logger.info("TELEGRAM_BOT_STARTING")
        gave_up = False
        try:
            while True:
                try:
                    await self._run_bot_once(token)
                    # _run_bot_once only returns normally after a clean stop request
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._last_error = str(e)
                    logger.error(f"TELEGRAM_BOT_ERROR: {e}")
                    if self._stop_requested:
                        break
                    if self._is_fatal_auth_error(e):
                        # Invalid/revoked token — retrying won't help; surface a
                        # clear "error" state instead of looping forever.
                        logger.error("TELEGRAM_BOT_AUTH_FAILED: invalid or revoked token, stopping retries")
                        gave_up = True
                        self._status = BotStatus.error
                        self._update_db_status(BotStatus.error)
                        break
                    self._retry_count += 1
                    delay = _backoff_delay(self._retry_count)
                    self._status = BotStatus.reconnecting
                    self._update_db_status(BotStatus.reconnecting)
                    logger.warning(
                        f"TELEGRAM_BOT_RECONNECTING: attempt {self._retry_count}, retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
                    if self._stop_requested:
                        break
        except asyncio.CancelledError:
            logger.info("Bot supervisor cancelled")
            raise
        finally:
            self._application = None
            if not gave_up:
                self._status = BotStatus.stopped
                self._update_db_status(BotStatus.stopped)
            logger.info("TELEGRAM_BOT_STOPPED" if not gave_up else "TELEGRAM_BOT_STOPPED_ON_ERROR")

    @staticmethod
    def _is_fatal_auth_error(exc: Exception) -> bool:
        try:
            from telegram.error import InvalidToken, Forbidden
            if isinstance(exc, (InvalidToken, Forbidden)):
                return True
        except Exception:
            pass
        msg = str(exc).lower()
        return "unauthorized" in msg or "invalid token" in msg or "not found" in msg and "bot" in msg

    async def _run_bot_once(self, token: str):
        """Run a single connection lifecycle. Raises on failure so the
        supervisor can decide whether/how to reconnect."""
        from bot.app import setup_application
        from database import SessionLocal as SF
        application = None
        try:
            application = await setup_application(token, SF)
            self._application = application
            me = await application.bot.get_me()
            self._bot_name = me.full_name
            self._bot_username = me.username
            self._retry_count = 0  # reset backoff after a successful (re)connect
            self._status = BotStatus.running
            self._update_db_status(BotStatus.running, bot_name=me.full_name, bot_username=me.username)
            logger.info(f"TELEGRAM_BOT_RUNNING: @{me.username}")
            await application.initialize()
            await application.start()
            await application.updater.start_polling(drop_pending_updates=True)
            while not self._stop_requested:
                await asyncio.sleep(1)
        finally:
            if application:
                try:
                    if application.updater and application.updater.running:
                        await application.updater.stop()
                    if application.running:
                        await application.stop()
                    await application.shutdown()
                except Exception as e:
                    logger.error(f"Bot shutdown error: {e}")
            if self._application is application:
                self._application = None

    async def stop_bot(self):
        self._stop_requested = True
        if self._bot_task and not self._bot_task.done():
            self._bot_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._bot_task), timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._status = BotStatus.stopped
        self._application = None
        self._update_db_status(BotStatus.stopped)

    async def restart_bot(self, token: str):
        await self.stop_bot()
        await asyncio.sleep(1)
        await self.start_bot(token)

    async def send_message(self, chat_id: str, text: str):
        if self._application and self.is_running():
            try:
                await self._application.bot.send_message(chat_id=int(chat_id), text=text)
                return True
            except Exception as e:
                logger.error(f"Send message error: {e}")
        return False


bot_manager = BotManager.get_instance()
