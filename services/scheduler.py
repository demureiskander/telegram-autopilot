from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from database.db import get_pending_scheduled, mark_scheduled_sent, mark_scheduled_failed
from logger import logger

scheduler = AsyncIOScheduler()


async def _send_scheduled(bot: Bot) -> None:
    """Проверяет и отправляет запланированные сообщения."""
    pending = await get_pending_scheduled()

    if pending:
        logger.info(f"[SCHEDULER] tick — found {len(pending)} pending message(s)")
    else:
        logger.debug("[SCHEDULER] tick — nothing to send")
        return

    for msg_id, owner_id, chat_id, text, business_connection_id in pending:
        try:
            kwargs = {"chat_id": chat_id, "text": text}
            if business_connection_id:
                kwargs["business_connection_id"] = business_connection_id

            await bot.send_message(**kwargs)
            await mark_scheduled_sent(msg_id)
            logger.info(
                f"[SCHEDULER] ✅ sent msg_id={msg_id} "
                f"owner_id={owner_id} chat_id={chat_id} "
                f"text_len={len(text)}"
            )
        except Exception as e:
            await mark_scheduled_failed(msg_id)
            logger.error(
                f"[SCHEDULER] ❌ failed msg_id={msg_id} "
                f"owner_id={owner_id} chat_id={chat_id} "
                f"error={e}"
            )


def start_scheduler(bot: Bot) -> None:
    scheduler.add_job(
        _send_scheduled,
        trigger=IntervalTrigger(minutes=1),
        args=[bot],
        id="send_scheduled",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[SCHEDULER] started — checking every 60 seconds")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()
        logger.info("[SCHEDULER] stopped")
