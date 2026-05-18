from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from logger import logger
from database.db import (
    get_user, update_user_setting,
    get_stats, get_chat_list, get_chat_messages,
    get_note, get_notes, set_user_note, delete_user_note, delete_note,
    clear_history, clear_all_history,
    get_trial_days_left, get_subscription_days_left,
    check_daily_limit, get_today_messages, TRIAL_DAYS,
)
from services.llm import AVAILABLE_MODELS

router = Router()


class AdminFSM(StatesGroup):
    editing_prompt = State()
    editing_note   = State()


_prompt_buffer: dict = {"text": "", "task": None}


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def kb_main(enabled: bool) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "✅ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data="adm:toggle")],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
            InlineKeyboardButton(text="💬 Чаты",       callback_data="adm:chats:0"),
        ],
        [
            InlineKeyboardButton(text="✏️ Промпт",    callback_data="adm:prompt_menu"),
            InlineKeyboardButton(text="🤖 Модель",     callback_data="adm:model_menu"),
        ],
        [
            InlineKeyboardButton(text="🗑 История",    callback_data="adm:clear_menu"),
            InlineKeyboardButton(text="💳 Подписка",   callback_data="billing:menu"),
        ],
        [
            InlineKeyboardButton(text="📅 Планировщик", callback_data="adm:schedule"),
        ],
    ])


def kb_back(dest: str = "adm:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=dest)]
    ])


@router.message(Command("admin"))
@router.message(Command("start"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Сначала напиши /start")
        return

    enabled = bool(user["is_enabled"])
    status = "✅ включён" if enabled else "⏸ выключен"

    trial_days = await get_trial_days_left(user_id)
    sub_days = await get_subscription_days_left(user_id)
    within, used, limit = await check_daily_limit(user_id, user["plan"] or "personal")

    if sub_days > 0:
        access = f"💳 Подписка: ещё <b>{sub_days} дн.</b>"
    elif trial_days > 0:
        access = f"🎁 Триал: ещё <b>{trial_days} из {TRIAL_DAYS} дн.</b>"
    else:
        access = "⚠️ <b>Доступ истёк</b> — оформи подписку"

    await message.answer(
        f"🛠 <b>Панель управления</b>\n\n"
        f"Автоответ: {status}\n"
        f"{access}\n"
        f"Сообщений сегодня: {used}/{limit}",
        reply_markup=kb_main(enabled)
    )


@router.callback_query(F.data == "adm:menu")
async def on_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    if not user:
        return

    enabled = bool(user["is_enabled"])
    status = "✅ включён" if enabled else "⏸ выключен"

    trial_days = await get_trial_days_left(user_id)
    sub_days = await get_subscription_days_left(user_id)
    within, used, limit = await check_daily_limit(user_id, user["plan"] or "personal")

    if sub_days > 0:
        access = f"💳 Подписка: ещё <b>{sub_days} дн.</b>"
    elif trial_days > 0:
        access = f"🎁 Триал: ещё <b>{trial_days} из {TRIAL_DAYS} дн.</b>"
    else:
        access = "⚠️ <b>Доступ истёк</b>"

    await safe_edit(callback,
        f"🛠 <b>Панель управления</b>\n\n"
        f"Автоответ: {status}\n"
        f"{access}\n"
        f"Сообщений сегодня: {used}/{limit}",
        reply_markup=kb_main(enabled)
    )


@router.callback_query(F.data == "adm:toggle")
async def on_toggle(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    if not user:
        return

    # Проверяем подключение перед включением
    from database.db import is_connected as _is_connected
    if not await _is_connected(user_id) and not user["is_enabled"]:
        await safe_edit(callback,
            "Бот ещё не подключён к твоему профилю — включить автоответ пока не получится.\n\n"
            "Подключи бота через:\n"
            "<b>Настройки → Автоматизация чатов → Добавить бота</b>\n"
            "Введи <code>@YourBotUsername</code>\n\n"
            "Как только подключишь — нажми кнопку ниже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подключил", callback_data="adm:check_connect")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")],
            ])
        )
        return

    new_val = 0 if user["is_enabled"] else 1
    await update_user_setting(user_id, "is_enabled", new_val)
    logger.info(f'[ADMIN] user_id={user_id} auto_reply={"ON" if new_val else "OFF"}')

    enabled = bool(new_val)
    status = "✅ включён" if enabled else "⏸ выключен"
    trial_days = await get_trial_days_left(user_id)
    sub_days = await get_subscription_days_left(user_id)
    within, used, limit = await check_daily_limit(user_id, user["plan"] or "personal")

    if sub_days > 0:
        access = f"💳 Подписка: ещё <b>{sub_days} дн.</b>"
    elif trial_days > 0:
        access = f"🎁 Триал: ещё <b>{trial_days} из {TRIAL_DAYS} дн.</b>"
    else:
        access = "⚠️ <b>Доступ истёк</b>"

    await safe_edit(callback,
        f"🛠 <b>Панель управления</b>\n\n"
        f"Автоответ: {status}\n"
        f"{access}\n"
        f"Сообщений сегодня: {used}/{limit}",
        reply_markup=kb_main(enabled)
    )


@router.callback_query(F.data == "adm:stats")
async def on_stats(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    s = await get_stats(user_id)
    user = await get_user(user_id)
    within, used, limit = await check_daily_limit(user_id, user["plan"] or "personal")

    await safe_edit(callback,
        "📊 <b>Статистика</b>\n\n"
        f"💬 Уникальных чатов: <b>{s['total_chats']}</b>\n"
        f"📨 Входящих всего: <b>{s['total_incoming']}</b>\n"
        f"📤 Ответов всего: <b>{s['total_replies']}</b>\n\n"
        f"📅 Сегодня: <b>{s['today_msgs']}</b> сообщений\n"
        f"📊 Лимит сегодня: <b>{used}/{limit}</b>",
        reply_markup=kb_back()
    )


@router.callback_query(F.data.startswith("adm:chats:"))
async def on_chats(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    page = int(callback.data.split(":")[2])
    per_page = 7
    chats = await get_chat_list(user_id)

    if not chats:
        await safe_edit(callback, "💬 Пока нет ни одного чата.", reply_markup=kb_back())
        return

    total = len(chats)
    start = page * per_page
    chunk = chats[start:start + per_page]

    buttons = []
    for chat_id, name, msg_count, last_active, last_msg, note in chunk:
        note_icon = "📝 " if note else ""
        label = f"{note_icon}{name or chat_id} · {msg_count} сообщ."
        buttons.append([InlineKeyboardButton(
            text=label[:42],
            callback_data=f"adm:chat:{chat_id}:{page}"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:chats:{page-1}"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:chats:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")])

    await safe_edit(callback,
        f"💬 <b>Чаты</b> ({total} всего) · стр. {page + 1}\n"
        f"<i>📝 — есть заметка</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("adm:chat:"))
async def on_chat_detail(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    msgs = await get_chat_messages(user_id, chat_id, limit=8)
    notes = await get_notes(user_id, chat_id)
    user_note = notes["user_note"]
    ai_note = notes["ai_note"]

    header = f"💬 <b>Чат {chat_id}</b>\n\n"

    # Заметка пользователя
    if user_note:
        header += f"📝 <b>Ваша заметка:</b>\n<tg-spoiler>{user_note}</tg-spoiler>\n\n"
    else:
        header += "📝 <b>Ваша заметка:</b> <i>не добавлена</i>\n\n"

    # Заметка ИИ
    if ai_note:
        header += f"🤖 <b>Заметка ассистента:</b>\n<tg-spoiler>{ai_note}</tg-spoiler>\n\n"
    else:
        header += "🤖 <b>Заметка ассистента:</b> <i>пока ничего</i>\n\n"

    if msgs:
        lines = []
        for role, content, created_at in msgs:
            time_str = created_at[11:16] if created_at else ""
            icon = "👤" if role == "user" else "🤖"
            short = content[:70] + ("…" if len(content) > 70 else "")
            lines.append(f"{icon} <i>{time_str}</i>  {short}")
        body = "\n\n".join(lines)
    else:
        body = "<i>Нет сообщений</i>"

    user_note_btn = "✏️ Изменить вашу заметку" if user_note else "📝 Добавить заметку"
    buttons = [
        [InlineKeyboardButton(text="📋 Сводка за сегодня", callback_data=f"adm:summary:{chat_id}:{page}")],
        [InlineKeyboardButton(text=user_note_btn, callback_data=f"adm:note_edit:{chat_id}:{page}")],
    ]
    if user_note:
        buttons.append([InlineKeyboardButton(
            text="🗑 Удалить вашу заметку",
            callback_data=f"adm:note_del:{chat_id}:{page}"
        )])
    buttons.append([InlineKeyboardButton(
        text="🗑 Очистить историю чата",
        callback_data=f"adm:clearchat:{chat_id}:{page}"
    )])
    buttons.append([InlineKeyboardButton(text="◀️ К списку", callback_data=f"adm:chats:{page}")])

    await safe_edit(callback, header + body,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("adm:note_edit:"))
async def on_note_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    notes = await get_notes(callback.from_user.id, chat_id)
    await state.set_state(AdminFSM.editing_note)
    await state.update_data(chat_id=chat_id, page=page)

    text = f"📝 <b>Ваша заметка</b> о чате <code>{chat_id}</code>\n\n"
    if notes["user_note"]:
        text += f"Текущая:\n<tg-spoiler>{notes['user_note']}</tg-spoiler>\n\n"
    if notes["ai_note"]:
        text += (
            f"🤖 Заметка ассистента:\n<tg-spoiler>{notes['ai_note']}</tg-spoiler>\n\n"
            f"<i>Совет: скопируй заметку ассистента и дополни её своими данными — "
            f"тогда ничего не потеряется.</i>\n\n"
        )
    text += (
        f"Например: <i>Рауль, знакомый, общаемся неформально</i>\n\n"
        f"Отмена → /admin"
    )
    await callback.message.answer(text)


@router.message(AdminFSM.editing_note)
async def on_note_input(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    page = data.get("page", 0)
    await state.clear()
    await set_user_note(message.from_user.id, chat_id, message.text.strip())
    await message.answer(
        "✅ Ваша заметка сохранена",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← К чату", callback_data=f"adm:chat:{chat_id}:{page}")]
        ])
    )


@router.callback_query(F.data.startswith("adm:note_del:"))
async def on_note_delete(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    await delete_user_note(callback.from_user.id, chat_id)
    await safe_edit(callback,
        "🗑 Ваша заметка удалена. Заметка ассистента сохранена.",
        reply_markup=kb_back(f"adm:chat:{chat_id}:{page}")
    )


@router.callback_query(F.data.startswith("adm:clearchat:"))
async def on_clear_chat(callback: CallbackQuery):
    await callback.answer("Очищено")
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    await clear_history(callback.from_user.id, chat_id)
    await safe_edit(callback,
        f"🗑 История чата очищена.",
        reply_markup=kb_back(f"adm:chats:{page}")
    )


@router.callback_query(F.data == "adm:prompt_menu")
async def on_prompt_menu(callback: CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    prompt = user["system_prompt"] or "не задан"
    await safe_edit(callback,
        f"✏️ <b>Системный промпт</b>\n\n"
        f"<code>{prompt[:2000]}{'…' if len(prompt) > 2000 else ''}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="adm:prompt_edit")],
            [InlineKeyboardButton(text="◀️ Назад",    callback_data="adm:menu")],
        ])
    )


@router.callback_query(F.data == "adm:prompt_edit")
async def on_prompt_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminFSM.editing_prompt)
    await callback.message.answer(
        "✏️ Напиши новый системный промпт.\n\n"
        "Если он длинный и Telegram разобьёт на несколько сообщений — "
        "бот подождёт и сохранит всё вместе.\n\n"
        "Отмена → /admin"
    )


@router.message(AdminFSM.editing_prompt)
async def on_prompt_input(message: Message, state: FSMContext):
    import asyncio as _asyncio

    if _prompt_buffer["text"]:
        _prompt_buffer["text"] += "\n" + message.text
    else:
        _prompt_buffer["text"] = message.text

    if _prompt_buffer["task"] and not _prompt_buffer["task"].done():
        _prompt_buffer["task"].cancel()

    async def _save():
        try:
            await _asyncio.sleep(5)
            full_text = _prompt_buffer["text"].strip()
            _prompt_buffer["text"] = ""
            _prompt_buffer["task"] = None
            if not full_text:
                return
            await state.clear()
            await update_user_setting(message.from_user.id, "system_prompt", full_text)
            await message.answer(
                f"✅ Промпт обновлён ({len(full_text)} символов)",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="← В меню", callback_data="adm:menu")]
                ])
            )
        except _asyncio.CancelledError:
            pass

    _prompt_buffer["task"] = _asyncio.create_task(_save())


@router.callback_query(F.data == "adm:model_menu")
async def on_model_menu(callback: CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)
    current = user["active_model"] or "deepseek-v4-flash"

    buttons = []
    for model_id, desc in AVAILABLE_MODELS.items():
        check = "✅ " if model_id == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{check}{desc}",
            callback_data=f"adm:model_set:{model_id}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")])

    await safe_edit(callback,
        "🤖 <b>Выбор модели</b>\n\n"
        "✅ — активная сейчас\n\n"
        "<b>DeepSeek V4 Flash</b> — лучший для бота-двойника, держит роль\n"
        "<b>Gemini 2.5 Flash Lite</b> — самый быстрый и дешёвый\n"
        "<b>DeepSeek V3.2</b> — стабильный запасной",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("adm:model_set:"))
async def on_model_set(callback: CallbackQuery):
    model_id = callback.data.split("adm:model_set:")[1]
    if model_id not in AVAILABLE_MODELS:
        await callback.answer("Неизвестная модель")
        return
    await update_user_setting(callback.from_user.id, "active_model", model_id)
    logger.info(f'[ADMIN] user_id={callback.from_user.id} model changed to {model_id}')
    await callback.answer("✅ Модель обновлена")
    await on_model_menu(callback)


@router.callback_query(F.data == "adm:clear_menu")
async def on_clear_menu(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback,
        "🗑 <b>Очистка истории</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Очистить всю историю", callback_data="adm:clearall_confirm")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")],
        ])
    )


@router.callback_query(F.data == "adm:clearall_confirm")
async def on_clearall_confirm(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback,
        "⚠️ Удалить всю историю чатов?\n\nОтменить нельзя.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data="adm:clearall_do")],
            [InlineKeyboardButton(text="◀️ Отмена",      callback_data="adm:clear_menu")],
        ])
    )


@router.callback_query(F.data == "adm:clearall_do")
async def on_clearall_do(callback: CallbackQuery):
    await callback.answer("Готово")
    await clear_all_history(callback.from_user.id)
    logger.info(f'[ADMIN] user_id={callback.from_user.id} cleared all history')
    await safe_edit(callback, "🗑 История очищена.", reply_markup=kb_back())


@router.message(Command("on"))
async def cmd_on(message: Message):
    from database.db import is_access_allowed
    allowed, _ = await is_access_allowed(message.from_user.id)
    if not allowed:
        await message.answer("⚠️ Доступ истёк — оформи подписку → /start")
        return
    await update_user_setting(message.from_user.id, "is_enabled", 1)
    await message.answer("✅ Автоответ включён")


@router.message(Command("off"))
async def cmd_off(message: Message):
    await update_user_setting(message.from_user.id, "is_enabled", 0)
    await message.answer("⏸ Автоответ выключен")


@router.message(Command("status"))
async def cmd_status(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Сначала напиши /start")
        return
    enabled = "✅ включён" if user["is_enabled"] else "⏸ выключен"
    trial_days = await get_trial_days_left(user_id)
    sub_days = await get_subscription_days_left(user_id)
    if sub_days > 0:
        access = f"💳 Подписка: ещё {sub_days} дн."
    elif trial_days > 0:
        access = f"🎁 Триал: ещё {trial_days} дн."
    else:
        access = "⚠️ Доступ истёк"
    await message.answer(
        f"Автоответ: {enabled}\n{access}\nТариф: {user['plan']}"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🛠 <b>Команды</b>\n\n"
        "/admin — панель управления\n"
        "/on — включить автоответ\n"
        "/off — выключить автоответ\n"
        "/status — статус и тариф\n"
        "/help — эта справка"
    )


# ── Проверка подключения из админ панели ──────────────────────────────────────

@router.callback_query(F.data == "adm:check_connect")
async def on_check_connect(callback: CallbackQuery):
    await callback.answer()
    from database.db import is_connected, set_connected
    user_id = callback.from_user.id
    connected = await is_connected(user_id)
    logger.info(f'[ADMIN] user_id={user_id} check_connect={connected}')

    if connected:
        await callback.message.answer("✅ Бот подключён к твоему профилю!")
        await on_menu(callback, state=None)
    else:
        await safe_edit(callback,
            "Бот пока не видит подключения к твоему профилю.\n\n"
            "Проверь что всё сделано правильно:\n"
            "<b>Настройки → Автоматизация чатов → Добавить бота</b>\n"
            "Введи <code>@YourBotUsername</code>\n\n"
            "Как только подключишь — нажми кнопку ниже.\n"
            "Без подключения ассистент не сможет видеть входящие сообщения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подключил", callback_data="adm:check_connect")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")],
            ])
        )


# ── Сводка диалога ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:summary:"))
async def on_summary(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    user_id = callback.from_user.id

    from database.db import get_today_messages, get_chat_list
    from services.llm import generate_summary

    # Получаем имя контакта
    chats = await get_chat_list(user_id, limit=100)
    contact_name = str(chat_id)
    for row in chats:
        if row[0] == chat_id:
            contact_name = row[1] or str(chat_id)
            break

    messages = await get_today_messages(user_id, chat_id)

    if not messages:
        await callback.answer("Сегодня сообщений не было", show_alert=True)
        return

    # Показываем индикатор загрузки
    await safe_edit(callback,
        f"📋 <b>Генерирую сводку...</b>\n\n"
        f"Анализирую {len(messages)} сообщений за сегодня.",
        reply_markup=None
    )

    summary = await generate_summary(messages, contact_name)

    if not summary:
        await safe_edit(callback,
            "⚠️ Не удалось сгенерировать сводку. Попробуйте позже.",
            reply_markup=kb_back(f"adm:chat:{chat_id}:{page}")
        )
        return

    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%Y")

    await safe_edit(callback,
        f"📋 <b>Сводка диалога с {contact_name}</b>\n"
        f"<i>{today} · {len(messages)} сообщений</i>\n\n"
        f"{summary}",
        reply_markup=kb_back(f"adm:chat:{chat_id}:{page}")
    )
