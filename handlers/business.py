import asyncio
from aiogram import Router, Bot
from aiogram.types import Message, BusinessConnection

from config import MAX_HISTORY
from database.db import (
    get_user, is_access_allowed, check_daily_limit,
    increment_daily_usage, get_history, save_message,
    upsert_chat, get_note, set_note, set_connected, get_chat_messages,
    log_event,
)
from services.llm import ask_llm, extract_contact_info
from logger import logger

router = Router()


@router.business_connection()
async def on_business_connection(event: BusinessConnection, bot: Bot):
    if event.is_enabled:
        logger.info(f"[CONNECT] user_id={event.user.id} connected profile")
        await set_connected(event.user.id, True)
        await log_event('connect_profile', event.user.id)
        await bot.send_message(
            event.user.id,
            "✅ Бот подключён к профилю. Можешь вернуться к настройке.",
        )
    else:
        logger.info(f"[DISCONNECT] user_id={event.user.id} disconnected profile")
        await set_connected(event.user.id, False)
        await log_event('disconnect_profile', event.user.id)
        await bot.send_message(
            event.user.id,
            "❌ Бот отключён от профиля."
        )


@router.business_message()
async def handle_business_message(message: Message, bot: Bot):
    owner_id = await _get_owner_id(message, bot)
    if not owner_id:
        logger.warning(f"[MSG] Could not get owner_id for business_connection_id={message.business_connection_id}")
        return

    if message.from_user and message.from_user.id == owner_id:
        return

    if not message.text:
        return

    user = await get_user(owner_id)
    if not user:
        logger.warning(f"[MSG] owner_id={owner_id} not found in DB")
        return

    if not user["is_enabled"]:
        logger.debug(f"[MSG] owner_id={owner_id} auto-reply is OFF, skipping")
        return

    allowed, reason = await is_access_allowed(owner_id)
    if not allowed:
        logger.info(f"[ACCESS] owner_id={owner_id} access denied: {reason}")
        await bot.send_message(
            owner_id,
            "⚠️ Твой триал или подписка истекли.\n"
            "Оформи подписку чтобы ассистент продолжил работу → /start",
        )
        return

    plan = user["plan"] or "personal"
    within_limit, used, limit = await check_daily_limit(owner_id, plan)
    if not within_limit:
        logger.info(f"[LIMIT] owner_id={owner_id} daily limit reached {used}/{limit}")
        await bot.send_message(
            owner_id,
            f"📊 Дневной лимит исчерпан ({used}/{limit} сообщений).\n"
            f"Обновится завтра в 00:00 UTC.",
        )
        return

    chat_id = message.chat.id

    # Собираем метаданные собеседника
    sender_name = ""
    sender_username = ""
    if message.from_user:
        parts = []
        if message.from_user.first_name:
            parts.append(message.from_user.first_name)
        if message.from_user.last_name:
            parts.append(message.from_user.last_name)
        sender_name = " ".join(parts).strip()
        sender_username = f"@{message.from_user.username}" if message.from_user.username else "нет username"

    logger.info(f"[MSG] owner_id={owner_id} chat_id={chat_id} from='{sender_name}'")

    await upsert_chat(owner_id, chat_id, sender_name or str(chat_id))
    await save_message(owner_id, chat_id, "user", message.text)
    await increment_daily_usage(owner_id)

    try:
        await bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
            business_connection_id=message.business_connection_id,
        )
    except Exception as e:
        logger.debug(f"[TYPING] Failed: {e}")

    # Считаем сколько раз уже общались
    prev_messages = await get_chat_messages(owner_id, chat_id, limit=100)
    msg_count = len(prev_messages)
    is_first = msg_count <= 1  # текущее сообщение уже сохранено

    # Заметка о контакте
    note = await get_note(owner_id, chat_id)

    # Строим системный промпт с чёткой структурой
    owner_prompt = user["system_prompt"] or ""

    system_prompt = (
        "=== ТВОЯ РОЛЬ ===\n"
        "Ты — AI-ассистент встроенный в Telegram профиль пользователя. "
        "Твоя задача: читать входящие сообщения и отвечать от его имени. "
        "Ты говоришь от первого лица как владелец профиля. "
        "Никогда не раскрывай что ты ИИ.\n\n"
        "=== О ВЛАДЕЛЬЦЕ ПРОФИЛЯ ===\n"
        f"{owner_prompt}\n\n"
        "=== КТО СЕЙЧАС ПИШЕТ ===\n"
        f"Имя: {sender_name or 'неизвестно'}\n"
        f"Username: {sender_username}\n"
        f"ID чата: {chat_id}\n"
    )

    if note:
        system_prompt += (
            f"Что ты знаешь об этом человеке: {note}\n"
        )
    else:
        system_prompt += (
            "Что известно об этом человеке: ничего — первый раз пишет или заметок нет.\n"
            "Не делай предположений о нём. Не переноси свой контекст на собеседника.\n"
        )

    if is_first:
        system_prompt += "Это первое сообщение от этого человека — отвечай нейтрально.\n"
    else:
        system_prompt += f"Вы уже общались, это сообщение #{msg_count}.\n"

    system_prompt += (
        "\n=== ФОРМАТ ОТВЕТА ===\n"
        "Можешь отправить 1-3 коротких сообщения через |||. "
        "По умолчанию одно. Никогда не ставь ||| в конце."
    )

    history = await get_history(owner_id, chat_id, limit=MAX_HISTORY)
    active_model = user["active_model"] or "deepseek-v4-flash"

    try:
        reply = await ask_llm(system_prompt, history, message.text, active_model=active_model)
        logger.info(f"[LLM] owner_id={owner_id} model={active_model} ok")
        await log_event('message_handled', owner_id, meta=active_model)
    except Exception as e:
        logger.error(f"[LLM] owner_id={owner_id} error: {e}", exc_info=True)
        await log_event('llm_error', owner_id)
        await bot.send_message(owner_id, f"⚠️ Ошибка при генерации ответа:\n{e}")
        return

    if not reply or not reply.strip():
        logger.warning(f"[LLM] owner_id={owner_id} empty reply")
        return

    await save_message(owner_id, chat_id, "assistant", reply)

    # Автоматическое извлечение информации о собеседнике
    asyncio.create_task(_update_contact_note(
        owner_id, chat_id, note, history, message.text, active_model
    ))

    # Убираем разделители если LLM вдруг их вставила
    clean_reply = reply.replace("|||", " ").strip()

    await bot.send_message(
        chat_id=chat_id,
        text=clean_reply,
        business_connection_id=message.business_connection_id,
    )

    logger.info(f"[SENT] owner_id={owner_id} chat_id={chat_id}")


async def _get_owner_id(message: Message, bot: Bot) -> int | None:
    try:
        conn = await bot.get_business_connection(message.business_connection_id)
        return conn.user.id
    except Exception as e:
        logger.error(f"[CONN] get_business_connection error: {e}")
        return None


async def _update_contact_note(
    owner_id: int,
    chat_id: int,
    current_note: str,
    history: list[dict],
    new_message: str,
    active_model: str,
) -> None:
    """Фоновая задача — анализирует сообщение и обновляет заметку если нужно."""
    try:
        new_note = await extract_contact_info(
            current_note, history, new_message, active_model
        )
        if new_note and new_note != current_note:
            await set_note(owner_id, chat_id, new_note)
            logger.info(
                f"[NOTE] owner_id={owner_id} chat_id={chat_id} "
                f"auto-updated note: '{new_note[:80]}'"
            )
    except Exception as e:
        logger.warning(f"[NOTE] owner_id={owner_id} extract failed: {e}")
