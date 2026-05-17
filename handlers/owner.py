import os
import gzip
import shutil
import tempfile
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from aiogram.exceptions import TelegramBadRequest

from config import OWNER_ID, DB_PATH
from database.db import (
    get_owner_stats, get_users_list,
    ban_user, unban_user, delete_user_data,
    clean_old_events, log_event,
)
from logger import logger

router = Router()

MILESTONES = {10, 50, 100, 500, 1000, 5000}


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def kb_owner() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика",  callback_data="ow:stats"),
            InlineKeyboardButton(text="📈 Активность",  callback_data="ow:activity"),
        ],
        [
            InlineKeyboardButton(text="💳 Финансы",     callback_data="ow:finance"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="ow:users:0"),
        ],
        [
            InlineKeyboardButton(text="🗄 База данных",  callback_data="ow:db"),
            InlineKeyboardButton(text="🧹 Очистка",     callback_data="ow:cleanup"),
        ],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="ow:menu")]
    ])


# ── /owneradmin ───────────────────────────────────────────────────────────────

@router.message(Command("owneradmin"), F.from_user.id == OWNER_ID)
async def cmd_owner_admin(message: Message):
    s = await get_owner_stats()
    await message.answer(
        "👑 <b>Панель владельца</b>\n\n"
        f"👥 Пользователей: <b>{s['total_users']}</b>\n"
        f"💳 Платных: <b>{s['paid_users']}</b> · 🎁 Триал: <b>{s['trial_users']}</b>\n"
        f"📨 Сообщений сегодня: <b>{s['messages_today']}</b>",
        reply_markup=kb_owner()
    )


@router.callback_query(F.data == "ow:menu", F.from_user.id == OWNER_ID)
async def on_owner_menu(callback: CallbackQuery):
    await callback.answer()
    s = await get_owner_stats()
    await safe_edit(callback,
        "👑 <b>Панель владельца</b>\n\n"
        f"👥 Пользователей: <b>{s['total_users']}</b>\n"
        f"💳 Платных: <b>{s['paid_users']}</b> · 🎁 Триал: <b>{s['trial_users']}</b>\n"
        f"📨 Сообщений сегодня: <b>{s['messages_today']}</b>",
        reply_markup=kb_owner()
    )


# ── Статистика ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ow:stats", F.from_user.id == OWNER_ID)
async def on_stats(callback: CallbackQuery):
    await callback.answer()
    s = await get_owner_stats()
    pct = s["pct_fn"]

    await safe_edit(callback,
        "📊 <b>Статистика</b>\n\n"

        "👥 <b>Пользователи</b>\n"
        f"Всего: <b>{s['total_users']}</b>\n"
        f"💳 Платных: <b>{s['paid_users']}</b>\n"
        f"  ├ Personal: <b>{s['personal_paid']}</b>\n"
        f"  └ Business: <b>{s['business_paid']}</b>\n"
        f"🎁 На триале: <b>{s['trial_users']}</b>\n"
        f"💤 Истёк доступ: <b>{s['expired_users']}</b>\n\n"

        "🔌 <b>Подключение и активность</b>\n"
        f"Подключили профиль: <b>{s['connected_users']}</b> ({pct(s['connected_users'], s['total_users'])})\n"
        f"Автоответ включён: <b>{s['active_users']}</b> ({pct(s['active_users'], s['connected_users'])})\n\n"

        "🎯 <b>Воронка онбординга</b>\n"
        f"Начали: <b>{s['onboard_started']}</b>\n"
        f"Завершили: <b>{s['onboard_done']}</b> ({s['onboard_conv']})\n"
        f"Подключили профиль: <b>{s['connected_ever']}</b> ({s['connect_conv']})\n\n"

        "🆕 <b>Прирост</b>\n"
        f"Сегодня: <b>{s['new_today']}</b> · За неделю: <b>{s['new_week']}</b>",
        reply_markup=kb_back()
    )


# ── Активность ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ow:activity", F.from_user.id == OWNER_ID)
async def on_activity(callback: CallbackQuery):
    await callback.answer()
    s = await get_owner_stats()
    pct = s["pct_fn"]

    # Динамика за 7 дней
    daily_text = ""
    if s["daily_msgs"]:
        daily_text = "\n\n📅 <b>Сообщений по дням (7д):</b>\n"
        max_cnt = max(c for _, c in s["daily_msgs"]) or 1
        for d, cnt in s["daily_msgs"]:
            bar_len = round(cnt / max_cnt * 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            daily_text += f"{d[5:]} {bar} {cnt}\n"

    # Модели
    model_text = ""
    if s["model_stats"]:
        model_text = "\n\n🤖 <b>Использование моделей:</b>\n"
        for model, cnt in s["model_stats"]:
            model_text += f"  {model}: <b>{cnt}</b>\n"

    await safe_edit(callback,
        "📈 <b>Активность</b>\n\n"

        "⏱ <b>DAU / WAU / MAU</b>\n"
        f"За 24ч: <b>{s['dau']}</b> ({pct(s['dau'], s['mau'])} от MAU)\n"
        f"За 7д:  <b>{s['wau']}</b> ({pct(s['wau'], s['mau'])} от MAU)\n"
        f"За 30д: <b>{s['mau']}</b>\n\n"

        "💬 <b>Сообщения</b>\n"
        f"Всего обработано: <b>{s['total_messages']}</b>\n"
        f"Сегодня: <b>{s['messages_today']}</b>\n\n"

        "⚠️ <b>Ошибки LLM</b>\n"
        f"Всего ошибок: <b>{s['llm_errors']}</b>\n"
        f"Fallback срабатываний: <b>{s['llm_fallbacks']}</b>"
        f"{model_text}"
        f"{daily_text}",
        reply_markup=kb_back()
    )


# ── Финансы ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ow:finance", F.from_user.id == OWNER_ID)
async def on_finance(callback: CallbackQuery):
    await callback.answer()
    s = await get_owner_stats()

    # Примерный перевод Stars → USD (1 Star ≈ $0.013)
    def to_usd(stars: int) -> str:
        return f"~${stars * 0.013:.2f}"

    await safe_edit(callback,
        "💳 <b>Финансы</b>\n\n"

        "⭐ <b>Telegram Stars</b>\n"
        f"Всего: <b>{s['total_stars']} ⭐</b> ({to_usd(s['total_stars'])})\n"
        f"За месяц: <b>{s['stars_month']} ⭐</b> ({to_usd(s['stars_month'])})\n"
        f"Сегодня: <b>{s['stars_today']} ⭐</b>\n"
        f"Транзакций: <b>{s['total_payments']}</b>\n\n"

        "📦 <b>Активные подписки</b>\n"
        f"Personal: <b>{s['personal_paid']}</b>\n"
        f"Business: <b>{s['business_paid']}</b>\n"
        f"Итого платных: <b>{s['paid_users']}</b>",
        reply_markup=kb_back()
    )


# ── Пользователи ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ow:users:"), F.from_user.id == OWNER_ID)
async def on_users(callback: CallbackQuery):
    await callback.answer()
    page = int(callback.data.split(":")[2])
    per_page = 8
    users = await get_users_list(limit=per_page, offset=page * per_page)

    if not users:
        await safe_edit(callback, "Нет пользователей.", reply_markup=kb_back())
        return

    lines = []
    for u in users:
        user_id, username, first_name, plan, is_enabled, is_connected, trial_at, sub_until, created = u
        name = f"@{username}" if username else first_name or str(user_id)
        status = "✅" if is_enabled else "⏸"
        conn = "🔌" if is_connected else "❌"
        plan_icon = "🏢" if plan == "business" else "👤"
        lines.append(f"{status}{conn}{plan_icon} {name} <code>{user_id}</code>")

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ow:users:{page-1}"))
    if len(users) == per_page:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ow:users:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ow:menu")])

    await safe_edit(callback,
        f"👥 <b>Пользователи</b> · стр. {page + 1}\n\n"
        + "\n".join(lines) + "\n\n"
        "<i>Для бана: /banuser ID\nДля разбана: /unbanuser ID\nДля удаления: /deleteuser ID</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# ── База данных ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ow:db", F.from_user.id == OWNER_ID)
async def on_db(callback: CallbackQuery):
    await callback.answer()
    size_kb = os.path.getsize(DB_PATH) // 1024 if os.path.exists(DB_PATH) else 0
    await safe_edit(callback,
        f"🗄 <b>База данных</b>\n\n"
        f"Размер: <b>{size_kb} КБ</b>\n"
        f"Путь: <code>{DB_PATH}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Экспорт БД", callback_data="ow:db_export")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="ow:menu")],
        ])
    )


@router.callback_query(F.data == "ow:db_export", F.from_user.id == OWNER_ID)
async def on_db_export(callback: CallbackQuery, bot: Bot):
    await callback.answer("Готовлю файл...")
    if not os.path.exists(DB_PATH):
        await callback.message.answer("❌ БД не найдена.")
        return

    size_kb = os.path.getsize(DB_PATH) // 1024
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    tmp = tempfile.NamedTemporaryFile(suffix=".db.gz", delete=False)
    tmp.close()
    with open(DB_PATH, "rb") as f_in, gzip.open(tmp.name, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    gz_kb = os.path.getsize(tmp.name) // 1024

    try:
        await bot.send_document(
            chat_id=callback.from_user.id,
            document=FSInputFile(tmp.name, filename="raveli_bot.db.gz"),
            caption=(
                f"📦 <b>Экспорт БД</b>\n\n"
                f"Оригинал: {size_kb} КБ\n"
                f"Сжатый: {gz_kb} КБ\n"
                f"Дата: {now}"
            )
        )
    finally:
        os.unlink(tmp.name)


# ── Очистка ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "ow:cleanup", F.from_user.id == OWNER_ID)
async def on_cleanup(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback,
        "🧹 <b>Очистка</b>\n\n"
        "Выбери действие:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить события старше 90 дней", callback_data="ow:clean_events:90")],
            [InlineKeyboardButton(text="🗑 Удалить события старше 30 дней", callback_data="ow:clean_events:30")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="ow:menu")],
        ])
    )


@router.callback_query(F.data.startswith("ow:clean_events:"), F.from_user.id == OWNER_ID)
async def on_clean_events(callback: CallbackQuery):
    await callback.answer()
    days = int(callback.data.split(":")[2])
    count = await clean_old_events(days)
    logger.info(f"[OWNER] cleaned {count} events older than {days} days")
    await safe_edit(callback,
        f"✅ Удалено <b>{count}</b> событий старше {days} дней.",
        reply_markup=kb_back()
    )


# ── Текстовые команды ──────────────────────────────────────────────────────────

@router.message(Command("banuser"), F.from_user.id == OWNER_ID)
async def cmd_ban(message: Message):
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: <code>/banuser 123456789</code>")
        return
    user_id = int(args[1])
    await ban_user(user_id)
    logger.info(f"[OWNER] banned user_id={user_id}")
    await message.answer(f"🚫 Пользователь <code>{user_id}</code> забанен.")


@router.message(Command("unbanuser"), F.from_user.id == OWNER_ID)
async def cmd_unban(message: Message):
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: <code>/unbanuser 123456789</code>")
        return
    user_id = int(args[1])
    await unban_user(user_id)
    logger.info(f"[OWNER] unbanned user_id={user_id}")
    await message.answer(f"✅ Пользователь <code>{user_id}</code> разбанен.")


@router.message(Command("deleteuser"), F.from_user.id == OWNER_ID)
async def cmd_delete(message: Message):
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: <code>/deleteuser 123456789</code>")
        return
    user_id = int(args[1])
    await delete_user_data(user_id)
    logger.info(f"[OWNER] deleted all data for user_id={user_id}")
    await message.answer(f"🗑 Все данные пользователя <code>{user_id}</code> удалены.")


@router.message(Command("milestone_check"), F.from_user.id == OWNER_ID)
async def cmd_milestone(message: Message):
    """Проверить и отправить milestone если нужно."""
    from database.db import get_owner_stats
    s = await get_owner_stats()
    total = s["total_users"]
    await message.answer(f"Всего пользователей: <b>{total}</b>")
