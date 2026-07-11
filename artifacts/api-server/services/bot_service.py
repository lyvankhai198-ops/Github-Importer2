import asyncio
import logging
from datetime import datetime
from typing import Optional
from database import SessionLocal
from models import TelegramBotConfig, BotStatus
from crypto import decrypt

logger = logging.getLogger(__name__)


class BotManager:
    _instance: Optional["BotManager"] = None

    def __init__(self):
        self._bot_task: Optional[asyncio.Task] = None
        self._application = None
        self._status = BotStatus.stopped
        self._bot_name = ""
        self._bot_username = ""

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
        if self.is_running():
            logger.info("Bot already running")
            return
        self._status = BotStatus.starting
        self._update_db_status(BotStatus.starting)
        self._bot_task = asyncio.create_task(self._run_bot(token))

    async def _run_bot(self, token: str):
        from bot.app import setup_application
        from database import SessionLocal as SF
        try:
            self._application = await setup_application(token, SF)
            me = await self._application.bot.get_me()
            self._bot_name = me.full_name
            self._bot_username = me.username
            self._status = BotStatus.running
            self._update_db_status(BotStatus.running, bot_name=me.full_name, bot_username=me.username)
            await self._application.initialize()
            await self._application.start()
            await self._application.updater.start_polling(drop_pending_updates=True)
            # Keep running until cancelled
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Bot task cancelled")
        except Exception as e:
            logger.error(f"Bot error: {e}")
            self._status = BotStatus.error
            self._update_db_status(BotStatus.error)
        finally:
            try:
                if self._application:
                    if self._application.updater and self._application.updater.running:
                        await self._application.updater.stop()
                    if self._application.running:
                        await self._application.stop()
                    await self._application.shutdown()
            except Exception as e:
                logger.error(f"Bot shutdown error: {e}")
            if self._status != BotStatus.error:
                self._status = BotStatus.stopped
                self._update_db_status(BotStatus.stopped)

    async def stop_bot(self):
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
