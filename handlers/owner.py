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
            InlineKeyboardButton(text="📢 Рассылка",     callback_data="ow:broadcast"),
            InlineKeyboardButton(text="🗄 База данных",  callback_data="ow:db"),
        ],
        [
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

    buttons = []
    for u in users:
        user_id, username, first_name, plan, is_enabled, is_connected, trial_at, sub_until, created = u
        name = f"@{username}" if username else first_name or str(user_id)
        status = "✅" if is_enabled else "⏸"
        conn = "🔌" if is_connected else "❌"
        plan_icon = "🏢" if plan == "business" else "👤"
        buttons.append([InlineKeyboardButton(
            text=f"{status}{conn}{plan_icon} {name}",
            callback_data=f"ow:user:{user_id}:{page}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ow:users:{page-1}"))
    if len(users) == per_page:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ow:users:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ow:menu")])

    await safe_edit(callback,
        f"👥 <b>Пользователи</b> · стр. {page + 1}\n"
        f"<i>Нажмите на пользователя для управления</i>",
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


@router.callback_query(F.data.startswith("ow:user:"), F.from_user.id == OWNER_ID)
async def on_user_detail(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3] if len(parts) > 3 else "0"

    from database.db import get_user
    user = await get_user(target_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    name = f"@{user['username']}" if user.get('username') else user.get('first_name') or str(target_id)
    plan = user.get('plan', 'personal')
    is_enabled = bool(user.get('is_enabled'))
    is_connected = bool(user.get('is_connected'))
    is_banned = bool(user.get('is_banned'))
    sub_until = user.get('subscription_until', '')
    trial_at = user.get('trial_started_at', '')
    created = user.get('created_at', '')[:10] if user.get('created_at') else '—'

    status_lines = (
        f"{'✅' if is_enabled else '⏸'} Автоответ: {'вкл' if is_enabled else 'выкл'}\n"
        f"{'🔌' if is_connected else '❌'} Профиль: {'подключён' if is_connected else 'не подключён'}\n"
        f"{'🚫' if is_banned else '✓'} Бан: {'да' if is_banned else 'нет'}\n"
        f"📦 Тариф: {plan}\n"
        f"📅 Зарегистрирован: {created}\n"
    )
    if sub_until:
        status_lines += f"💳 Подписка до: {sub_until[:10]}\n"

    ban_btn_text = "✅ Разбанить" if is_banned else "🚫 Забанить"
    ban_cb = f"ow:unban:{target_id}:{back_page}" if is_banned else f"ow:ban:{target_id}:{back_page}"

    has_sub = bool(sub_until and sub_until > datetime.utcnow().isoformat()[:19])

    await safe_edit(callback,
        f"👤 <b>{name}</b>\n"
        f"<code>{target_id}</code>\n\n"
        f"{status_lines}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Выдать премиум", callback_data=f"ow:grant:{target_id}:{back_page}")],
            [InlineKeyboardButton(text="❌ Отозвать подписку", callback_data=f"ow:revoke:{target_id}:{back_page}")] if has_sub else [],
            [InlineKeyboardButton(text=ban_btn_text, callback_data=ban_cb)],
            [InlineKeyboardButton(text="🗑 Удалить все данные", callback_data=f"ow:delete_confirm:{target_id}:{back_page}")],
            [InlineKeyboardButton(text="◀️ К списку", callback_data=f"ow:users:{back_page}")],
        ])
    )


@router.callback_query(F.data.startswith("ow:ban:"), F.from_user.id == OWNER_ID)
async def on_ban(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]
    await ban_user(target_id)
    logger.info(f"[OWNER] banned user_id={target_id}")
    await callback.answer("🚫 Пользователь забанен", show_alert=True)
    # Обновляем карточку
    callback.data = f"ow:user:{target_id}:{back_page}"
    await on_user_detail(callback)


@router.callback_query(F.data.startswith("ow:unban:"), F.from_user.id == OWNER_ID)
async def on_unban(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]
    await unban_user(target_id)
    logger.info(f"[OWNER] unbanned user_id={target_id}")
    await callback.answer("✅ Пользователь разбанен", show_alert=True)
    callback.data = f"ow:user:{target_id}:{back_page}"
    await on_user_detail(callback)


@router.callback_query(F.data.startswith("ow:delete_confirm:"), F.from_user.id == OWNER_ID)
async def on_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]

    await safe_edit(callback,
        f"⚠️ <b>Удалить все данные пользователя <code>{target_id}</code>?</b>\n\n"
        f"Будут удалены:\n"
        f"— Профиль и настройки\n"
        f"— История всех чатов\n"
        f"— Заметки о контактах\n"
        f"— Статистика использования\n\n"
        f"<b>Это действие необратимо.</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить всё", callback_data=f"ow:delete_do:{target_id}:{back_page}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"ow:user:{target_id}:{back_page}")],
        ])
    )


@router.callback_query(F.data.startswith("ow:delete_do:"), F.from_user.id == OWNER_ID)
async def on_delete_do(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]

    await delete_user_data(target_id)
    logger.info(f"[OWNER] deleted all data for user_id={target_id}")

    await safe_edit(callback,
        f"✅ Все данные пользователя <code>{target_id}</code> удалены.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К списку", callback_data=f"ow:users:{back_page}")]
        ])
    )


# ── Ручная выдача / отзыв премиума ───────────────────────────────────────────

@router.callback_query(F.data.startswith("ow:grant:"), F.from_user.id == OWNER_ID)
async def on_grant_menu(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3] if len(parts) > 3 else "0"

    await safe_edit(callback,
        f"💳 <b>Выдать премиум</b>\n\n"
        f"Пользователь: <code>{target_id}</code>\n\n"
        f"Выберите тариф:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Personal", callback_data=f"ow:grant_plan:{target_id}:{back_page}:personal"),
                InlineKeyboardButton(text="🏢 Business", callback_data=f"ow:grant_plan:{target_id}:{back_page}:business"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"ow:user:{target_id}:{back_page}")],
        ])
    )


@router.callback_query(F.data.startswith("ow:grant_plan:"), F.from_user.id == OWNER_ID)
async def on_grant_plan(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]
    plan = parts[4]
    plan_name = "Personal" if plan == "personal" else "Business"

    await safe_edit(callback,
        f"💳 <b>{plan_name}</b> для <code>{target_id}</code>\n\n"
        f"Выберите срок:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="7 дней",    callback_data=f"ow:grant_do:{target_id}:{back_page}:{plan}:week")],
            [InlineKeyboardButton(text="1 месяц",   callback_data=f"ow:grant_do:{target_id}:{back_page}:{plan}:month")],
            [InlineKeyboardButton(text="3 месяца",  callback_data=f"ow:grant_do:{target_id}:{back_page}:{plan}:quarter")],
            [InlineKeyboardButton(text="1 год",     callback_data=f"ow:grant_do:{target_id}:{back_page}:{plan}:year")],
            [InlineKeyboardButton(text="♾ Бессрочно (10 лет)", callback_data=f"ow:grant_do:{target_id}:{back_page}:{plan}:forever")],
            [InlineKeyboardButton(text="◀️ Назад",  callback_data=f"ow:grant:{target_id}:{back_page}")],
        ])
    )


@router.callback_query(F.data.startswith("ow:grant_do:"), F.from_user.id == OWNER_ID)
async def on_grant_do(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]
    plan = parts[4]
    period = parts[5]

    from database.db import extend_subscription, PERIOD_LABELS
    from datetime import datetime, timedelta
    import aiosqlite
    from config import DB_PATH

    if period == "forever":
        # 10 лет поверх текущей подписки
        from database.db import get_user
        user = await get_user(target_id)
        now = datetime.utcnow()
        if user and user.get("subscription_until"):
            try:
                current = datetime.fromisoformat(user["subscription_until"])
                base = max(current, now)
            except Exception:
                base = now
        else:
            base = now
        forever = (base + timedelta(days=3650)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET subscription_until = ?, plan = ? WHERE user_id = ?",
                (forever, plan, target_id)
            )
            await db.commit()
        period_label = "бессрочно"
    else:
        await extend_subscription(target_id, period, plan)
        period_label = PERIOD_LABELS.get(period, period)

    plan_name = "Personal" if plan == "personal" else "Business"
    logger.info(f"[OWNER] granted {plan_name} {period_label} to user_id={target_id}")

    await safe_edit(callback,
        f"✅ <b>Премиум выдан!</b>\n\n"
        f"Пользователь: <code>{target_id}</code>\n"
        f"Тариф: <b>{plan_name}</b>\n"
        f"Срок: <b>{period_label}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К пользователю", callback_data=f"ow:user:{target_id}:{back_page}")]
        ])
    )


@router.callback_query(F.data.startswith("ow:revoke:"), F.from_user.id == OWNER_ID)
async def on_revoke(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_id = int(parts[2])
    back_page = parts[3]

    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET subscription_until = NULL WHERE user_id = ?",
            (target_id,)
        )
        await db.commit()

    logger.info(f"[OWNER] revoked subscription for user_id={target_id}")
    await callback.answer("✅ Подписка отозвана", show_alert=True)

    callback.data = f"ow:user:{target_id}:{back_page}"
    await on_user_detail(callback)


# ── Рассылка ─────────────────────────────────────────────────────────────────

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


class BroadcastFSM(StatesGroup):
    waiting_segment = State()
    waiting_ids     = State()
    waiting_text    = State()
    confirm         = State()


def kb_broadcast_segments() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Всем пользователям",    callback_data="bc:seg:all")],
        [InlineKeyboardButton(text="👤 Только Personal",       callback_data="bc:seg:personal")],
        [InlineKeyboardButton(text="🏢 Только Business",       callback_data="bc:seg:business")],
        [InlineKeyboardButton(text="🎁 Только на триале",      callback_data="bc:seg:trial")],
        [InlineKeyboardButton(text="✏️ Выборочно (ID/username)", callback_data="bc:seg:manual")],
        [InlineKeyboardButton(text="◀️ Назад",                 callback_data="ow:menu")],
    ])


@router.callback_query(F.data == "ow:broadcast", F.from_user.id == OWNER_ID)
async def on_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit(callback,
        "📢 <b>Рассылка</b>\n\n"
        "Кому отправить сообщение?",
        reply_markup=kb_broadcast_segments()
    )
    await state.set_state(BroadcastFSM.waiting_segment)


@router.callback_query(F.data.startswith("bc:seg:"), BroadcastFSM.waiting_segment, F.from_user.id == OWNER_ID)
async def on_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    segment = callback.data.split(":")[2]
    await state.update_data(segment=segment)

    if segment == "manual":
        await safe_edit(callback,
            "✏️ <b>Выборочная рассылка</b>\n\n"
            "Введите ID или username через запятую или с новой строки:\n\n"
            "<i>Пример:\n123456789\n@username\n987654321</i>\n\n"
            "Отмена → /owneradmin"
        )
        await state.set_state(BroadcastFSM.waiting_ids)
    else:
        labels = {
            "all":      "всем пользователям",
            "personal": "Personal пользователям",
            "business": "Business пользователям",
            "trial":    "пользователям на триале",
        }
        await safe_edit(callback,
            f"📢 Рассылка <b>{labels[segment]}</b>\n\n"
            "Напишите текст сообщения:\n\n"
            "<i>Поддерживается HTML-форматирование: <b>жирный</b>, <i>курсив</i>, <code>код</code></i>\n\n"
            "Отмена → /owneradmin"
        )
        await state.set_state(BroadcastFSM.waiting_text)


@router.message(BroadcastFSM.waiting_ids, F.from_user.id == OWNER_ID)
async def on_broadcast_ids(message: Message, state: FSMContext):
    raw = message.text.strip()
    ids = []
    for item in raw.replace(",", "\n").split("\n"):
        item = item.strip()
        if not item:
            continue
        if item.startswith("@"):
            ids.append(item)
        elif item.lstrip("-").isdigit():
            ids.append(int(item))

    if not ids:
        await message.answer("❌ Не распознал ни одного ID. Попробуйте ещё раз:")
        return

    await state.update_data(manual_ids=ids)
    await message.answer(
        f"✅ Получателей: <b>{len(ids)}</b>\n\n"
        "Напишите текст сообщения:\n\n"
        "<i>Поддерживается HTML-форматирование</i>\n\n"
        "Отмена → /owneradmin"
    )
    await state.set_state(BroadcastFSM.waiting_text)


@router.message(BroadcastFSM.waiting_text, F.from_user.id == OWNER_ID)
async def on_broadcast_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    data = await state.get_data()
    segment = data.get("segment", "all")
    manual_ids = data.get("manual_ids", [])

    segment_labels = {
        "all":      "все пользователи",
        "personal": "Personal",
        "business": "Business",
        "trial":    "на триале",
        "manual":   f"выборочно ({len(manual_ids)} чел.)",
    }

    await message.answer(
        f"📢 <b>Проверьте рассылку:</b>\n\n"
        f"Сегмент: <b>{segment_labels.get(segment, segment)}</b>\n\n"
        f"Текст:\n{message.text}\n\n"
        "Отправить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="bc:send")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="bc:edit_text")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="ow:menu")],
        ])
    )
    await state.set_state(BroadcastFSM.confirm)


@router.callback_query(F.data == "bc:edit_text", BroadcastFSM.confirm, F.from_user.id == OWNER_ID)
async def on_broadcast_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(BroadcastFSM.waiting_text)
    await callback.message.answer("✏️ Напишите новый текст сообщения:")


@router.callback_query(F.data == "bc:send", BroadcastFSM.confirm, F.from_user.id == OWNER_ID)
async def on_broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data()
    segment = data.get("segment", "all")
    text = data.get("text", "")
    manual_ids = data.get("manual_ids", [])
    await state.clear()

    # Получаем список получателей
    import aiosqlite
    from config import DB_PATH

    user_ids = []

    if segment == "manual":
        for item in manual_ids:
            if isinstance(item, int):
                user_ids.append(item)
            else:
                # username — пробуем резолвить
                try:
                    chat = await bot.get_chat(item)
                    user_ids.append(chat.id)
                except Exception:
                    logger.warning(f"[BROADCAST] failed to resolve {item}")
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            if segment == "all":
                query = "SELECT user_id FROM users"
                params = ()
            elif segment == "personal":
                query = "SELECT user_id FROM users WHERE plan = 'personal' AND subscription_until > datetime('now')"
                params = ()
            elif segment == "business":
                query = "SELECT user_id FROM users WHERE plan = 'business' AND subscription_until > datetime('now')"
                params = ()
            elif segment == "trial":
                query = """SELECT user_id FROM users
                           WHERE trial_started_at >= datetime('now', '-16 days')
                           AND (subscription_until IS NULL OR subscription_until <= datetime('now'))"""
                params = ()

            async with db.execute(query, params) as c:
                rows = await c.fetchall()
                user_ids = [r[0] for r in rows]

    if not user_ids:
        await callback.message.answer("⚠️ Нет пользователей в выбранном сегменте.")
        return

    # Отправляем
    status_msg = await callback.message.answer(
        f"📤 Отправляю... 0/{len(user_ids)}"
    )

    sent = 0
    failed = 0

    for i, uid in enumerate(user_ids):
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[BROADCAST] failed to send to {uid}: {e}")

        # Обновляем прогресс каждые 10 сообщений
        if (i + 1) % 10 == 0:
            try:
                await status_msg.edit_text(
                    f"📤 Отправляю... {i+1}/{len(user_ids)}"
                )
            except Exception:
                pass

        # Антифлуд — пауза каждые 25 сообщений
        if (i + 1) % 25 == 0:
            import asyncio
            await asyncio.sleep(1)

    logger.info(f"[BROADCAST] done: sent={sent} failed={failed} segment={segment}")

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"✉️ Отправлено: <b>{sent}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>",
        reply_markup=kb_back()
    )
