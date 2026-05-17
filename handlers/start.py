import asyncio
from aiogram import Router, F
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
    get_trial_days_left, get_subscription_days_left,
    is_connected, log_event, set_user_timezone, TRIAL_DAYS,
)

router = Router()


class OnboardingFSM(StatesGroup):
    step_use_case = State()
    step_connect  = State()
    step_name     = State()
    step_city     = State()
    step_job      = State()
    step_style    = State()
    step_spam     = State()
    step_avoid    = State()
    step_confirm  = State()


# ── Хелперы ───────────────────────────────────────────────────────────────────

async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def kb_use_case() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Для себя", callback_data="ob:usecase:personal")],
        [InlineKeyboardButton(text="🏢 Для бизнеса", callback_data="ob:usecase:business")],
    ])


def kb_connected() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подключил", callback_data="ob:connected")],
        [InlineKeyboardButton(text="⏭ Сделаю позже", callback_data="ob:skip_connect")],
    ])


def kb_style() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Коротко и неформально", callback_data="ob:style:short")],
        [InlineKeyboardButton(text="💼 Чётко и по делу", callback_data="ob:style:business")],
        [InlineKeyboardButton(text="😊 Тепло и развёрнуто", callback_data="ob:style:warm")],
        [InlineKeyboardButton(text="✏️ Опишу сам", callback_data="ob:style:custom")],
    ])


def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустить!", callback_data="ob:confirm")],
        [InlineKeyboardButton(text="✏️ Изменить промпт", callback_data="ob:edit_prompt")],
    ])


def kb_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="ob:skip_step")],
    ])


def build_prompt(data: dict, use_case: str) -> str:
    name = data.get("name", "пользователь")
    job = data.get("job", "")
    style_key = data.get("style", "short")
    spam = data.get("spam", "игнорировать")
    avoid = data.get("avoid", "")

    style_map = {
        "short":    "Коротко и неформально. Строчные буквы, минимум знаков препинания.",
        "business": "Чётко и по делу. Вежливо, структурированно, без лишнего.",
        "warm":     "Тепло и развёрнуто. Проявляю интерес, иногда с юмором.",
        "custom":   data.get("style_custom", ""),
    }
    style_text = style_map.get(style_key, "")

    if use_case == "business":
        role = f"Ты — AI-ассистент который отвечает на входящие сообщения от имени {name}."
        ctx = f"Контекст: {job}." if job else ""
    else:
        role = f"Ты — {name}. Отвечаешь в Telegram вместо него/неё."
        ctx = f"Чем занимается: {job}." if job else ""

    avoid_text = f"\nТем избегать: {avoid}." if avoid else ""

    return (
        f"{role} Никогда не признавай что ты ИИ.\n\n"
        f"{ctx}\n\n"
        f"Стиль: {style_text}\n\n"
        f"Спам/реклама: {spam}.{avoid_text}\n\n"
        f"Не начинай с «Отлично!», «Конечно!», «Рад помочь!». "
        f"Не пиши длинные ответы там где хватит короткого."
    ).strip()


# ── Город → часовой пояс ──────────────────────────────────────────────────────

_TIMEZONE_OVERRIDES = {
    "istanbul": "Europe/Istanbul",
    "истамбул": "Europe/Istanbul",
    "стамбул": "Europe/Istanbul",
    "ankara": "Europe/Istanbul",
    "анкара": "Europe/Istanbul",
    "izmir": "Europe/Istanbul",
    "измир": "Europe/Istanbul",
    "beijing": "Asia/Shanghai",
    "пекин": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "гонконг": "Asia/Hong_Kong",
}

_POPULAR_CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Самара", "Омск", "Ростов-на-Дону", "Уфа",
    "Стамбул", "Анкара", "Баку", "Ташкент", "Алматы", "Бишкек",
    "Душанбе", "Ашхабад", "Минск", "Тбилиси", "Ереван",
    "Дубай", "Пекин", "Токио", "Лондон", "Берлин", "Париж",
    "Нью-Йорк", "Лос-Анджелес", "Сингапур", "Бангкок", "Самарканд",
]


def _suggest_city(city_input: str) -> list[str]:
    import difflib
    return difflib.get_close_matches(city_input, _POPULAR_CITIES, n=3, cutoff=0.5)


async def _resolve_timezone(city: str) -> tuple[str | None, str | None]:
    try:
        from geopy.geocoders import Nominatim
        from timezonefinder import TimezoneFinder
        import asyncio

        loop = asyncio.get_event_loop()
        geolocator = Nominatim(user_agent="raveli_bot_tz")
        tf = TimezoneFinder()

        queries = [city, f"{city} city center", f"city of {city}"]
        for query in queries:
            location = await loop.run_in_executor(
                None,
                lambda q=query: geolocator.geocode(q, language="en", timeout=10, exactly_one=True)
            )
            if not location:
                continue

            tz = tf.timezone_at(lng=location.longitude, lat=location.latitude)
            if not tz:
                tz = tf.closest_timezone_at(lng=location.longitude, lat=location.latitude)
            if tz:
                tz_override = _TIMEZONE_OVERRIDES.get(city.lower())
                if tz_override:
                    tz = tz_override
                loc_ru = await loop.run_in_executor(
                    None,
                    lambda: geolocator.geocode(city, language="ru", timeout=10, exactly_one=True)
                )
                city_display = loc_ru.address.split(",")[0].strip() if loc_ru else location.address.split(",")[0].strip()
                return tz, city_display

        return None, None
    except Exception:
        return None, None


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_user(user_id)
    first_name = message.from_user.first_name or "друг"

    default_prompt = "You are a personal AI assistant. Reply to messages on behalf of the user. Never reveal you are an AI."
    already_onboarded = (
        user and
        user.get("system_prompt") and
        user["system_prompt"] != default_prompt and
        user["system_prompt"] != ""
    )

    if already_onboarded:
        trial_days = await get_trial_days_left(user_id)
        sub_days = await get_subscription_days_left(user_id)
        if sub_days > 0:
            status = f"💳 Подписка активна ещё <b>{sub_days} дн.</b>"
        elif trial_days > 0:
            status = f"⏳ Триал: осталось <b>{trial_days} из {TRIAL_DAYS} дней</b>"
        else:
            status = "⚠️ Триал завершён — оформите подписку чтобы продолжить"
        await message.answer(
            f"С возвращением, {first_name}! 👋\n\n{status}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Открыть панель", callback_data="adm:menu")],
                [InlineKeyboardButton(text="💳 Подписка", callback_data="billing:menu")],
            ])
        )
        return

    await log_event('onboard_start', user_id)
    logger.info(f'[ONBOARD] user_id={user_id} started onboarding')

    await message.answer(
        f"Привет, {first_name}! 👋\n\n"
        f"<b>Raveli</b> — это ИИ-ассистент который отвечает на сообщения "
        f"в Telegram от вашего имени.\n\n"
        f"Настроим всё за пару минут.\n\n"
        f"Для чего планируете использовать?",
        reply_markup=kb_use_case()
    )
    await state.set_state(OnboardingFSM.step_use_case)


# ── Шаг 1 — Цель ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ob:usecase:"), OnboardingFSM.step_use_case)
async def ob_usecase(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    use_case = callback.data.split(":")[2]
    await state.update_data(use_case=use_case)
    await update_user_setting(callback.from_user.id, "plan", use_case)
    logger.info(f'[ONBOARD] user_id={callback.from_user.id} use_case={use_case}')

    await safe_edit(callback,
        "🔌 <b>Подключите ассистента к профилю</b>\n\n"
        "Откройте Telegram и перейдите:\n\n"
        "<b>Профиль → ✏️ → Автоматизация чатов → Добавить бота</b>\n\n"
        "Введите <code>@RavelioBot</code> и выберите к каким чатам "
        "он будет иметь доступ.\n\n"
        "Готово — нажмите кнопку ниже 👇",
        reply_markup=kb_connected()
    )
    await state.set_state(OnboardingFSM.step_connect)


# ── Шаг 2 — Подключение ───────────────────────────────────────────────────────

@router.callback_query(F.data == "ob:connected", OnboardingFSM.step_connect)
async def ob_connected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    connected = await is_connected(user_id)
    logger.info(f'[ONBOARD] user_id={user_id} check_connected={connected}')

    if not connected:
        await safe_edit(callback,
            "🤔 Бот ещё не видит подключения.\n\n"
            "Проверьте что всё сделано правильно:\n\n"
            "<b>Профиль → ✏️ → Автоматизация чатов → Добавить бота</b>\n"
            "Введите <code>@RavelioBot</code>\n\n"
            "Как подключите — нажмите кнопку снова.\n"
            "<i>Можно пропустить и подключить позже — но без этого ассистент не будет отвечать.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подключил, проверить снова", callback_data="ob:connected")],
                [InlineKeyboardButton(text="⏭ Пропустить, подключу позже", callback_data="ob:skip_connect")],
            ])
        )
        return

    await safe_edit(callback,
        "✅ <b>Подключено!</b>\n\n"
        "Как вас зовут?\n\n"
        "<i>Ассистент будет использовать ваше имя чтобы общаться в нужном стиле.</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_name)


@router.callback_query(F.data == "ob:skip_connect", OnboardingFSM.step_connect)
async def ob_skip_connect(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_edit(callback,
        "👤 <b>Как вас зовут?</b>\n\n"
        "<i>Ассистент будет использовать ваше имя чтобы общаться в нужном стиле.</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_name)


# ── Шаг 3 — Имя ───────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.step_name)
async def ob_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "🌍 <b>Из какого вы города?</b>\n\n"
        "<i>Нужно чтобы ассистент знал ваш часовой пояс — "
        "это важно для планировщика сообщений.</i>",
    )
    await state.set_state(OnboardingFSM.step_city)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_name)
async def ob_skip_name(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(name="пользователь")
    await safe_edit(callback,
        "🌍 <b>Из какого вы города?</b>\n\n"
        "<i>Нужно чтобы ассистент знал ваш часовой пояс — "
        "это важно для планировщика сообщений.</i>",
    )
    await state.set_state(OnboardingFSM.step_city)


# ── Шаг 4 — Город ─────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.step_city)
async def ob_city(message: Message, state: FSMContext):
    city_input = message.text.strip()
    await message.answer("⏳ Определяю часовой пояс...")

    tz, city_display = await _resolve_timezone(city_input)

    if not tz:
        suggestions = _suggest_city(city_input)
        if suggestions:
            suggestion_text = " / ".join(f"<b>{s}</b>" for s in suggestions)
            await message.answer(
                f"🤔 Не нашёл город «{city_input}».\n\n"
                f"Возможно, вы имели в виду: {suggestion_text}?\n\n"
                "Напишите ещё раз:"
            )
        else:
            await message.answer(
                f"🤔 Не нашёл город «{city_input}».\n\n"
                "Попробуйте написать на английском или уточните страну:\n"
                "<i>Istanbul / Самарканд, Узбекистан</i>\n\n"
                "Напишите ещё раз:"
            )
        return

    await set_user_timezone(message.from_user.id, tz)
    await state.update_data(city=city_display, timezone=tz)

    await message.answer(
        f"✅ <b>{city_display}</b> — {tz}\n\n"
        "💼 <b>Чем занимаетесь?</b>\n\n"
        "<i>Пара слов — чтобы ассистент отвечал в правильном контексте.</i>\n"
        "<i>Например: дизайнер-фрилансер / студент / менеджер</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_job)


# ── Шаг 5 — Работа ────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.step_job)
async def ob_job(message: Message, state: FSMContext):
    await state.update_data(job=message.text.strip())
    await message.answer(
        "💬 <b>Как вы обычно пишете?</b>",
        reply_markup=kb_style()
    )
    await state.set_state(OnboardingFSM.step_style)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_job)
async def ob_skip_job(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(job="")
    await safe_edit(callback,
        "💬 <b>Как вы обычно пишете?</b>",
        reply_markup=kb_style()
    )
    await state.set_state(OnboardingFSM.step_style)


# ── Шаг 6 — Стиль ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("ob:style:"), OnboardingFSM.step_style)
async def ob_style(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    style = callback.data.split(":")[2]

    if style == "custom":
        await safe_edit(callback, "✏️ Опишите свой стиль общения:")
        await state.update_data(style="custom")
        return

    await state.update_data(style=style)
    await safe_edit(callback,
        "🚫 <b>Что делать со спамом и рекламой?</b>\n\n"
        "<i>Например: игнорировать / ответить «не интересует»</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_spam)


@router.message(OnboardingFSM.step_style)
async def ob_style_custom(message: Message, state: FSMContext):
    await state.update_data(style="custom", style_custom=message.text.strip())
    await message.answer(
        "🚫 <b>Что делать со спамом и рекламой?</b>\n\n"
        "<i>Например: игнорировать / ответить «не интересует»</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_spam)


# ── Шаг 7 — Спам ──────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.step_spam)
async def ob_spam(message: Message, state: FSMContext):
    await state.update_data(spam=message.text.strip())
    await message.answer(
        "🙈 <b>Есть темы которых лучше избегать?</b>\n\n"
        "<i>Например: личные отношения / финансы / политика</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_avoid)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_spam)
async def ob_skip_spam(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(spam="игнорировать")
    await safe_edit(callback,
        "🙈 <b>Есть темы которых лучше избегать?</b>\n\n"
        "<i>Например: личные отношения / финансы / политика</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_avoid)


# ── Шаг 8 — Темы ──────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.step_avoid)
async def ob_avoid(message: Message, state: FSMContext):
    await state.update_data(avoid=message.text.strip())
    await _show_preview(message, state, via_message=True)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_avoid)
async def ob_skip_avoid(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(avoid="")
    await _show_preview(callback.message, state, via_message=False, callback=callback)


async def _show_preview(message, state, via_message=True, callback=None):
    data = await state.get_data()
    use_case = data.get("use_case", "personal")
    prompt = build_prompt(data, use_case)
    await state.update_data(built_prompt=prompt)

    text = (
        "📋 <b>Готово! Вот промпт для вашего ассистента:</b>\n\n"
        f"<code>{prompt[:600]}{'…' if len(prompt) > 600 else ''}</code>\n\n"
        "Запустить с этим промптом или изменить?"
    )

    if via_message:
        await message.answer(text, reply_markup=kb_confirm())
    else:
        await safe_edit(callback, text, reply_markup=kb_confirm())

    await state.set_state(OnboardingFSM.step_confirm)


# ── Шаг 9 — Подтверждение ─────────────────────────────────────────────────────

@router.callback_query(F.data == "ob:confirm")
async def ob_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    prompt = data.get("built_prompt", "")
    user_id = callback.from_user.id

    if not prompt:
        await callback.answer("Что-то пошло не так — попробуйте /start заново", show_alert=True)
        return

    await update_user_setting(user_id, "system_prompt", prompt)
    await state.clear()

    trial_days = await get_trial_days_left(user_id)
    await log_event('onboard_complete', user_id)
    logger.info(f'[ONBOARD] user_id={user_id} onboarding complete, prompt saved ({len(prompt)} chars)')

    await safe_edit(callback,
        f"🎉 <b>Ассистент настроен!</b>\n\n"
        f"У вас <b>{trial_days} дней</b> бесплатного доступа.\n\n"
        f"Включите автоответ — и ассистент начнёт работать 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Включить автоответ", callback_data="adm:toggle")],
            [InlineKeyboardButton(text="⚙️ Открыть панель", callback_data="adm:menu")],
        ])
    )


@router.callback_query(F.data == "ob:edit_prompt")
async def ob_edit_prompt(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    from handlers.admin import AdminFSM
    await state.set_state(AdminFSM.editing_prompt)
    await callback.message.answer(
        "✏️ Напишите новый промпт — сохраню его.\n\nОтмена → /start"
    )


# ── /help ──────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Как подключить ассистента</b>\n\n"
        "Откройте Telegram:\n\n"
        "<b>Профиль → ✏️ → Автоматизация чатов → Добавить бота</b>\n\n"
        "Введите <code>@RavelioBot</code> и выберите к каким чатам он имеет доступ.\n\n"
        "После подключения включите автоответ через /admin",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Открыть панель", callback_data="adm:menu")]
        ])
    )
