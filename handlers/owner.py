from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from config import OWNER_ID
from database.db import get_global_stats

router = Router()


@router.message(Command("owneradmin"), F.from_user.id == OWNER_ID)
async def cmd_owner_admin(message: Message):
    s = await get_global_stats()
    await message.answer(
        "👑 <b>Глобальная статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{s['total_users']}</b>\n"
        f"💳 Платных: <b>{s['paid_users']}</b>\n"
        f"🎁 На триале: <b>{s['trial_users']}</b>\n"
        f"📨 Всего сообщений: <b>{s['total_messages']}</b>\n"
        f"🆕 Новых сегодня: <b>{s['new_today']}</b>"
    )
