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
from services.llm import ask_llm
from logger import logger

router = Router()


class ScheduleFSM(StatesGroup):
    waiting_input = State()  # пользователь описывает задачу свободным текстом
    confirm       = State()  # подтверждение перед сохранением


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


async def _parse_schedule_request(
    user_input: str,
    user_tz: str,
    chat_list: list,
) -> dict | None:
    """
    LLM парсит свободный текст и возвращает структурированные данные.
    Возвращает dict с ключами: chat_id, chat_name, text, send_at_utc, send_at_local
    Или None если не удалось распарсить.
    """
    now_local = datetime.now(pytz.timezone(user_tz))
    now_str = now_local.strftime("%Y-%m-%d %H:%M (%A)")

    chats_text = ""
    if chat_list:
        chats_text = "Известные чаты пользователя:\n"
        for row in chat_list[:20]:
            chat_id, name = row[0], row[1]
            chats_text += f"- {name or 'без имени'} (chat_id: {chat_id})\n"

    prompt = (
        f"Сейчас: {now_str} (часовой пояс пользователя: {user_tz})\n\n"
        f"{chats_text}\n"
        f"Пользователь хочет запланировать отправку сообщения:\n"
        f"\"{user_input}\"\n\n"
        "Извлеки:\n"
        "1. chat_id — числовой ID чата из списка выше (если имя совпадает)\n"
        "2. chat_name — имя получателя как написал пользователь\n"
        "3. text — текст сообщения для отправки\n"
        "4. send_at_local — дата и время отправки в формате YYYY-MM-DD HH:MM\n"
        "   (в часовом поясе пользователя, учитывая 'сегодня', 'завтра', 'через час' и т.д.)\n\n"
        "Ответь СТРОГО в формате JSON без пояснений:\n"
        "{\"chat_id\": 123456, \"chat_name\": \"Имя\", \"text\": \"текст\", \"send_at_local\": \"2026-05-17 19:00\"}\n\n"
        "Если не можешь определить получателя или время — верни: {\"error\": \"причина\"}"
    )

    try:
        from config import LLM_API_KEY, LLM_BASE_URL
        import aiohttp
        import json

        async with aiohttp.ClientSession() as session:
            async with session.post(
                LLM_BASE_URL,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gemini-2.5-flash-lite",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 200,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
                raw = raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(raw)

                if "error" in parsed:
                    return {"error": parsed["error"]}

                # Конвертируем время в UTC
                tz = pytz.timezone(user_tz)
                local_dt = tz.localize(
                    datetime.strptime(parsed["send_at_local"], "%Y-%m-%d %H:%M")
                )
                utc_dt = local_dt.astimezone(pytz.utc)

                return {
                    "chat_id": parsed.get("chat_id"),
                    "chat_name": parsed.get("chat_name", "?"),
                    "text": parsed.get("text", ""),
                    "send_at_utc": utc_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "send_at_local": parsed["send_at_local"],
                }
    except Exception as e:
        logger.error(f"[SCHEDULER] parse error: {e}")
        return None


# ── Кнопка в админ панели ─────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:schedule")
async def on_schedule_menu(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback,
        "📅 <b>Планировщик сообщений</b>\n\n"
        "Запланируйте отправку сообщения в нужное время — бот отправит его автоматически.",
        reply_markup=kb_schedule_menu()
    )


# ── Новое запланированное сообщение ───────────────────────────────────────────

@router.callback_query(F.data == "sched:new")
async def on_schedule_new(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ScheduleFSM.waiting_input)
    await callback.message.answer(
        "✏️ <b>Новое запланированное сообщение</b>\n\n"
        "Напишите свободным текстом что, кому и когда отправить.\n\n"
        "<i>Примеры:\n"
        "— Напиши Рауле завтра в 19:00: привет, как дела?\n"
        "— Отправь маме через 2 часа: не забудь позвонить врачу\n"
        "— Напомни Артёму в пятницу в 10 утра про встречу</i>\n\n"
        "Отмена → /admin"
    )


@router.message(ScheduleFSM.waiting_input)
async def on_schedule_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        return

    user_tz = user.get("timezone") or "UTC"
    chat_list = await get_chat_list(user_id, limit=20)

    await message.answer("⏳ Разбираю запрос...")

    result = await _parse_schedule_request(message.text, user_tz, chat_list)

    if not result:
        await message.answer(
            "Не удалось разобрать запрос 🤔\n\n"
            "Попробуйте написать точнее, например:\n"
            "<i>Напиши Рауле завтра в 19:00: привет!</i>\n\n"
            "Или отмените → /admin"
        )
        return

    if "error" in result:
        await message.answer(
            f"Не смог разобрать: {result['error']}\n\n"
            "Попробуйте ещё раз или отмените → /admin"
        )
        return

    await state.update_data(
        result=result,
        business_connection_id=message.business_connection_id or "",
    )
    await state.set_state(ScheduleFSM.confirm)

    chat_name = result["chat_name"]
    text = result["text"]
    send_at = result["send_at_local"]

    await message.answer(
        f"📋 <b>Проверьте запланированное сообщение:</b>\n\n"
        f"👤 Кому: <b>{chat_name}</b>\n"
        f"🕐 Когда: <b>{send_at}</b> (ваше время)\n"
        f"💬 Текст: <i>{text}</i>\n\n"
        "Всё верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запланировать", callback_data="sched:confirm")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="sched:new")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:schedule")],
        ])
    )


@router.callback_query(F.data == "sched:confirm", ScheduleFSM.confirm)
async def on_schedule_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    result = data.get("result")
    business_connection_id = data.get("business_connection_id", "")

    if not result or not result.get("chat_id"):
        await callback.answer(
            "Не удалось определить получателя. Попробуйте ещё раз.",
            show_alert=True
        )
        await state.clear()
        return

    msg_id = await add_scheduled_message(
        owner_id=callback.from_user.id,
        chat_id=result["chat_id"],
        text=result["text"],
        send_at_utc=result["send_at_utc"],
        business_connection_id=business_connection_id,
    )

    await state.clear()
    logger.info(
        f"[SCHEDULER] scheduled msg_id={msg_id} "
        f"owner_id={callback.from_user.id} "
        f"send_at={result['send_at_utc']}"
    )

    await safe_edit(callback,
        f"✅ <b>Запланировано!</b>\n\n"
        f"Сообщение будет отправлено <b>{result['send_at_local']}</b> по вашему времени.",
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
            "У вас нет запланированных сообщений.",
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

        short_text = text[:30] + "…" if len(text) > 30 else text
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

    # Обновляем список
    callback.data = "sched:list"
    await on_schedule_list(callback)
