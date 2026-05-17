from datetime import datetime
import pytz
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from database.db import (
    get_user, get_chat_list, add_scheduled_message,
    get_user_scheduled, cancel_scheduled,
)
from logger import logger

router = Router()


class ScheduleFSM(StatesGroup):
    waiting_chat   = State()  # выбор получателя из списка
    waiting_manual = State()  # ввод username или ID вручную
    waiting_time   = State()  # ввод времени
    waiting_text   = State()  # ввод инструкции
    waiting_edit   = State()  # ручное редактирование
    confirm        = State()  # подтверждение


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def kb_schedule_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Запланировать сообщение", callback_data="sched:new")],
        [InlineKeyboardButton(text="📋 Мои запланированные", callback_data="sched:list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")],
    ])


async def _parse_time(time_input: str, user_tz: str) -> datetime | None:
    """
    Парсит время через LLM — понимает 'завтра в 19:00', 'через 2 часа', '15 мая в 10 утра' и т.д.
    Возвращает datetime в UTC или None.
    """
    try:
        from config import LLM_API_KEY, LLM_BASE_URL
        import aiohttp
        import json

        tz = pytz.timezone(user_tz)
        now_local = datetime.now(tz)
        now_str = now_local.strftime("%Y-%m-%d %H:%M (%A, %d %B %Y)")

        prompt = (
            f"Сейчас: {now_str} (часовой пояс: {user_tz})\n\n"
            f"Пользователь хочет отправить сообщение: \"{time_input}\"\n\n"
            "Определи дату и время отправки в формате YYYY-MM-DD HH:MM.\n"
            "Учитывай: 'завтра', 'послезавтра', 'через N часов/минут', "
            "'в пятницу', 'на следующей неделе', 'утром' (9:00), 'вечером' (19:00) и т.д.\n\n"
            "Ответь СТРОГО одной строкой в формате: YYYY-MM-DD HH:MM\n"
            "Если не можешь определить — ответь: ERROR"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                LLM_BASE_URL,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gemini-2.5-flash-lite",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 30,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"].strip()

                if "ERROR" in raw or not raw:
                    return None

                local_dt = tz.localize(datetime.strptime(raw[:16], "%Y-%m-%d %H:%M"))
                utc_dt = local_dt.astimezone(pytz.utc)
                return utc_dt

    except Exception as e:
        logger.error(f"[SCHEDULER] time parse error: {e}")
        return None


async def _compose_message(instruction: str, user_prompt: str) -> str | None:
    """LLM формулирует сообщение на основе инструкции пользователя."""
    try:
        from config import LLM_API_KEY, LLM_BASE_URL
        import aiohttp

        prompt = (
            f"Ты помогаешь пользователю составить сообщение которое он хочет отправить кому-то.\n\n"
            f"Контекст о пользователе (как он обычно общается):\n{user_prompt}\n\n"
            f"Инструкция пользователя: \"{instruction}\"\n\n"
            f"Составь одно готовое сообщение которое пользователь отправит получателю.\n"
            f"Сообщение должно звучать естественно, в стиле пользователя.\n"
            f"Верни ТОЛЬКО текст сообщения, без пояснений и кавычек."
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                LLM_BASE_URL,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gemini-2.5-flash-lite",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 300,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[SCHEDULER] compose error: {e}")
        return None


# ── Меню планировщика ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:schedule")
async def on_schedule_menu(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback,
        "📅 <b>Планировщик сообщений</b>\n\n"
        "Запланируйте отправку сообщения — бот отправит его автоматически в нужное время.",
        reply_markup=kb_schedule_menu()
    )


# ── Новое сообщение — выбор получателя ───────────────────────────────────────

@router.callback_query(F.data == "sched:new")
async def on_schedule_new(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    chats = await get_chat_list(user_id, limit=20)

    buttons = []
    if chats:
        for row in chats:
            chat_id, name, msg_count = row[0], row[1], row[2]
            display = name or f"Чат {chat_id}"
            buttons.append([InlineKeyboardButton(
                text=f"💬 {display}",
                callback_data=f"sched:chat:{chat_id}"
            )])

    # Всегда показываем кнопку ввода вручную
    buttons.append([InlineKeyboardButton(
        text="✏️ Ввести Username или ID",
        callback_data="sched:manual_input"
    )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:schedule")])

    header = "📅 <b>Кому отправить?</b>\n\nВыберите получателя из списка:"
    if not chats:
        header = (
            "📅 <b>Кому отправить?</b>\n\n"
            "История чатов пока пуста — введите Username или ID получателя вручную."
        )

    await safe_edit(callback, header,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(ScheduleFSM.waiting_chat)


@router.callback_query(F.data == "sched:manual_input")
async def on_manual_input(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ScheduleFSM.waiting_manual)
    await callback.message.answer(
        "✏️ <b>Введите Username или ID получателя</b>\n\n"
        "Варианты:\n"
        "— Username: <code>@username</code>\n"
        "— Числовой ID: <code>123456789</code>\n\n"
        "<i>Получить ID можно через @userinfobot</i>\n\n"
        "Отмена → /admin"
    )


@router.message(ScheduleFSM.waiting_manual)
async def on_manual_recipient(message: Message, state: FSMContext, bot):
    text = message.text.strip()

    if text.startswith("@"):
        # Резолвим username в числовой chat_id через Telegram API
        await message.answer("⏳ Проверяю получателя...")
        try:
            chat = await bot.get_chat(text)
            chat_id = chat.id
            chat_name = chat.full_name or text
            logger.info(f"[SCHEDULER] resolved {text} → chat_id={chat_id} name='{chat_name}'")
        except Exception as e:
            logger.warning(f"[SCHEDULER] failed to resolve username {text}: {e}")
            await message.answer(
                f"❌ Не удалось найти <code>{text}</code>\n\n"
                "<b>Почему так происходит:</b> Telegram не позволяет ботам искать "
                "пользователей по username если они ещё не писали через этого бота.\n\n"
                "<b>Как решить:</b>\n"
                "1. Попросите получателя написать вам хотя бы одно сообщение — "
                "тогда он появится в списке чатов\n"
                "2. Или узнайте его числовой ID через "
                "<a href=\"https://t.me/userinfobot\">@userinfobot</a> "
                "и введите цифрами\n\n"
                "Введите ID или попробуйте снова:"
            )
            return
    elif text.lstrip("-").isdigit():
        chat_id = int(text)
        chat_name = f"ID {text}"
    else:
        await message.answer(
            "❌ Не распознал формат.\n\n"
            "Введите <code>@username</code> или числовой ID вроде <code>123456789</code>:"
        )
        return

    await state.update_data(chat_id=chat_id, chat_name=chat_name)
    await state.set_state(ScheduleFSM.waiting_time)

    await message.answer(
        f"✅ Получатель: <b>{chat_name}</b> (<code>{chat_id}</code>)\n\n"
        "🕐 <b>Когда отправить?</b>\n\n"
        "Напишите время в любом удобном формате:\n\n"
        "<i>— завтра в 19:00\n"
        "— через 2 часа\n"
        "— в пятницу в 10 утра\n"
        "— 25 мая в 15:30</i>",
    )


@router.callback_query(F.data.startswith("sched:chat:"), ScheduleFSM.waiting_chat)
async def on_schedule_chat_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    chat_id = int(callback.data.split(":")[2])

    # Получаем имя из списка
    user_id = callback.from_user.id
    chats = await get_chat_list(user_id, limit=20)
    chat_name = str(chat_id)
    for row in chats:
        if row[0] == chat_id:
            chat_name = row[1] or str(chat_id)
            break

    await state.update_data(chat_id=chat_id, chat_name=chat_name)
    await state.set_state(ScheduleFSM.waiting_time)

    await safe_edit(callback,
        f"📅 Получатель: <b>{chat_name}</b>\n\n"
        "🕐 <b>Когда отправить?</b>\n\n"
        "Напишите время в любом удобном формате:\n\n"
        "<i>— завтра в 19:00\n"
        "— через 2 часа\n"
        "— в пятницу в 10 утра\n"
        "— 25 мая в 15:30</i>",
    )


# ── Ввод времени ──────────────────────────────────────────────────────────────

@router.message(ScheduleFSM.waiting_time)
async def on_schedule_time(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    user_tz = user.get("timezone") or "UTC" if user else "UTC"

    await message.answer("⏳ Определяю время...")

    utc_dt = await _parse_time(message.text, user_tz)

    if not utc_dt:
        await message.answer(
            "Не удалось определить время 🤔\n\n"
            "Попробуйте написать точнее:\n"
            "<i>— завтра в 19:00\n"
            "— через 2 часа\n"
            "— в пятницу в 10 утра</i>"
        )
        return

    # Показываем время в часовом поясе пользователя для подтверждения
    tz = pytz.timezone(user_tz)
    local_dt = utc_dt.astimezone(tz)
    local_str = local_dt.strftime("%d.%m.%Y в %H:%M")

    await state.update_data(
        send_at_utc=utc_dt.strftime("%Y-%m-%d %H:%M:%S"),
        send_at_local=local_str,
    )
    await state.set_state(ScheduleFSM.waiting_text)

    await message.answer(
        f"✅ Время: <b>{local_str}</b> (ваш часовой пояс)\n\n"
        "💬 <b>Что написать?</b>\n\n"
        "Опишите что хотите сказать — ИИ сам сформулирует сообщение и покажет вам на проверку.\n\n"
        "<i>Например: спроси как дела и напомни про встречу завтра</i>",
    )


# ── Ввод инструкции / текста ─────────────────────────────────────────────────

@router.message(ScheduleFSM.waiting_text)
async def on_schedule_text(message: Message, state: FSMContext):
    instruction = message.text.strip()
    await state.update_data(instruction=instruction)

    user = await get_user(message.from_user.id)
    user_prompt = user.get("system_prompt", "") if user else ""

    await message.answer("✍️ Формулирую сообщение...")

    composed = await _compose_message(instruction, user_prompt)

    if not composed:
        # Если LLM не смогла — используем инструкцию как есть
        composed = instruction
        await message.answer(
            "⚠️ Не удалось сформулировать автоматически — использую ваш текст как есть."
        )

    await state.update_data(text=composed)
    data = await state.get_data()
    await state.set_state(ScheduleFSM.confirm)

    await message.answer(
        "📋 <b>Проверьте сообщение:</b>\n\n"
        f"👤 Кому: <b>{data['chat_name']}</b>\n"
        f"🕐 Когда: <b>{data['send_at_local']}</b>\n\n"
        f"💬 <b>Текст:</b>\n<i>{composed}</i>\n\n"
        "Всё верно или хотите изменить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запланировать", callback_data="sched:confirm")],
            [InlineKeyboardButton(text="🔄 Переформулировать", callback_data="sched:recompose")],
            [InlineKeyboardButton(text="✏️ Написать вручную", callback_data="sched:edit_text")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:schedule")],
        ])
    )


@router.callback_query(F.data == "sched:recompose", ScheduleFSM.confirm)
async def on_recompose(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    instruction = data.get("instruction", "")

    user = await get_user(callback.from_user.id)
    user_prompt = user.get("system_prompt", "") if user else ""

    await callback.message.answer("✍️ Переформулирую...")

    composed = await _compose_message(instruction, user_prompt)
    if not composed:
        await callback.answer("Не удалось переформулировать", show_alert=True)
        return

    await state.update_data(text=composed)
    data = await state.get_data()

    await callback.message.answer(
        "📋 <b>Новый вариант:</b>\n\n"
        f"👤 Кому: <b>{data['chat_name']}</b>\n"
        f"🕐 Когда: <b>{data['send_at_local']}</b>\n\n"
        f"💬 <b>Текст:</b>\n<i>{composed}</i>\n\n"
        "Подходит?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запланировать", callback_data="sched:confirm")],
            [InlineKeyboardButton(text="🔄 Ещё раз", callback_data="sched:recompose")],
            [InlineKeyboardButton(text="✏️ Написать вручную", callback_data="sched:edit_text")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:schedule")],
        ])
    )


@router.callback_query(F.data == "sched:edit_text", ScheduleFSM.confirm)
async def on_edit_text(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ScheduleFSM.waiting_edit)
    data = await state.get_data()
    await callback.message.answer(
        f"✏️ Напишите текст сообщения вручную:\n\n"
        f"<i>Текущий вариант: {data.get('text', '')}</i>"
    )


@router.message(ScheduleFSM.waiting_edit)
async def on_manual_edit(message: Message, state: FSMContext):
    await state.update_data(text=message.text.strip())
    data = await state.get_data()
    await state.set_state(ScheduleFSM.confirm)

    await message.answer(
        "📋 <b>Проверьте:</b>\n\n"
        f"👤 Кому: <b>{data['chat_name']}</b>\n"
        f"🕐 Когда: <b>{data['send_at_local']}</b>\n\n"
        f"💬 <b>Текст:</b>\n<i>{data['text']}</i>\n\n"
        "Всё верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запланировать", callback_data="sched:confirm")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="sched:edit_text")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:schedule")],
        ])
    )


# ── Подтверждение ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:confirm", ScheduleFSM.confirm)
async def on_schedule_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    user_id = callback.from_user.id

    # Берём business_connection_id из профиля пользователя
    user = await get_user(user_id)
    biz_conn_id = user.get("business_connection_id", "") if user else ""

    msg_id = await add_scheduled_message(
        owner_id=user_id,
        chat_id=data["chat_id"],
        text=data["text"],
        send_at_utc=data["send_at_utc"],
        business_connection_id=biz_conn_id,
    )

    await state.clear()
    logger.info(
        f"[SCHEDULER] scheduled msg_id={msg_id} "
        f"owner_id={user_id} chat_id={data['chat_id']} "
        f"send_at={data['send_at_utc']}"
    )

    await safe_edit(callback,
        f"✅ <b>Запланировано!</b>\n\n"
        f"Сообщение будет отправлено <b>{data['send_at_local']}</b>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои запланированные", callback_data="sched:list")],
            [InlineKeyboardButton(text="◀️ В панель", callback_data="adm:menu")],
        ])
    )


# ── Список запланированных ────────────────────────────────────────────────────

@router.callback_query(F.data == "sched:list")
async def on_schedule_list(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    user_tz = user.get("timezone") or "UTC" if user else "UTC"
    tz = pytz.timezone(user_tz)

    scheduled = await get_user_scheduled(user_id)

    if not scheduled:
        await safe_edit(callback,
            "📋 <b>Запланированные сообщения</b>\n\n"
            "Нет запланированных сообщений.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Запланировать", callback_data="sched:new")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:schedule")],
            ])
        )
        return

    buttons = []
    lines = []
    for msg_id, chat_id, text, send_at, status in scheduled:
        try:
            utc_dt = pytz.utc.localize(datetime.strptime(send_at, "%Y-%m-%d %H:%M:%S"))
            local_dt = utc_dt.astimezone(tz)
            time_str = local_dt.strftime("%d.%m %H:%M")
        except Exception:
            time_str = send_at[:16]

        short_text = text[:35] + "…" if len(text) > 35 else text
        lines.append(f"🕐 <b>{time_str}</b> — {short_text}")
        buttons.append([InlineKeyboardButton(
            text=f"❌ Отменить · {time_str}",
            callback_data=f"sched:cancel:{msg_id}"
        )])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:schedule")])

    await safe_edit(callback,
        "📋 <b>Запланированные сообщения</b>\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("sched:cancel:"))
async def on_schedule_cancel(callback: CallbackQuery):
    await callback.answer()
    msg_id = int(callback.data.split(":")[2])
    success = await cancel_scheduled(msg_id, callback.from_user.id)

    if success:
        await callback.answer("✅ Сообщение отменено", show_alert=True)
    else:
        await callback.answer("❌ Не удалось отменить", show_alert=True)

    callback.data = "sched:list"
    await on_schedule_list(callback)
