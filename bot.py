import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, DB_PATH
from services.scheduler import start_scheduler, stop_scheduler
from database.db import init_db
from middlewares.access import AccessMiddleware
from handlers import start, admin, billing, business, owner, schedule
from logger import logger


async def main():
    logger.info("Starting bot...")

    # Автосоздание директории для БД если не существует
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info(f"Created directory for DB: {db_dir}")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(AccessMiddleware())

    dp.include_router(owner.router)
    dp.include_router(start.router)
    dp.include_router(billing.router)
    dp.include_router(admin.router)
    dp.include_router(schedule.router)
    dp.include_router(business.router)

    try:
        await init_db()
        logger.info(f"Database initialized at {DB_PATH}")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}", exc_info=True)
        return

    start_scheduler(bot)
    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "callback_query",
                "pre_checkout_query",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
                "business_connection",
            ],
        )
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
    finally:
        stop_scheduler()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
