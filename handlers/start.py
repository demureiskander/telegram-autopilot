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


def kb_use_case() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Для себя лично", callback_data="ob:usecase:personal")],
        [InlineKeyboardButton(text="🏢 Для бизнеса / команды", callback_data="ob:usecase:business")],
    ])


def kb_connected() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подключил, продолжить", callback_data="ob:connected")],
        [InlineKeyboardButton(text="⏭ Пропустить, настрою позже", callback_data="ob:skip_connect")],
    ])


def kb_style() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Коротко и неформально", callback_data="ob:style:short")],
        [InlineKeyboardButton(text="💼 Чётко и по делу", callback_data="ob:style:business")],
        [InlineKeyboardButton(text="😊 Тепло и развёрнуто", callback_data="ob:style:warm")],
        [InlineKeyboardButton(text="✏️ Напишу сам", callback_data="ob:style:custom")],
    ])


def kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Всё верно, запустить!", callback_data="ob:confirm")],
        [InlineKeyboardButton(text="✏️ Изменить промпт вручную", callback_data="ob:edit_prompt")],
    ])


def kb_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="ob:skip_step")],
    ])


def kb_not_connected() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подключил", callback_data="adm:check_connect")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")],
    ])


def build_prompt(data: dict, use_case: str) -> str:
    name = data.get("name", "пользователь")
    job = data.get("job", "")
    style_key = data.get("style", "short")
    spam = data.get("spam", "игнорировать или ответить «не интересует»")
    avoid = data.get("avoid", "")

    style_map = {
        "short":    "Пишу коротко и неформально. Строчные буквы с друзьями, минимум знаков препинания.",
        "business": "Пишу чётко и по делу. Структурированно, вежливо, без лишних слов.",
        "warm":     "Пишу тепло и развёрнуто. Проявляю интерес к собеседнику, иногда с юмором.",
        "custom":   data.get("style_custom", ""),
    }
    style_text = style_map.get(style_key, "")

    if use_case == "business":
        role_text = f"Ты — AI-ассистент который отвечает на входящие сообщения от имени {name}."
        context = f"Контекст: {job}." if job else ""
    else:
        role_text = f"Ты — {name}. Отвечаешь в Telegram вместо него/неё."
        context = f"Чем занимается: {job}." if job else ""

    avoid_text = f"\nТемы которых лучше избегать: {avoid}." if avoid else ""

    return (
        f"{role_text} Никогда не признавай что ты ИИ.\n\n"
        f"{context}\n\n"
        f"Стиль общения: {style_text}\n\n"
        f"Спам и реклама: {spam}.{avoid_text}\n\n"
        f"Никогда не начинай с «Отлично!», «Конечно!», «Рад помочь!». "
        f"Не пиши длинные ответы там где хватит короткого."
    ).strip()


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


# ── /start ───────────────────────────────────────────────────────────────────

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
            status = f"Подписка активна ещё <b>{sub_days} дн.</b>"
        elif trial_days > 0:
            status = f"Триал: осталось <b>{trial_days} из {TRIAL_DAYS} дней</b>"
        else:
            status = "Триал завершён — оформи подписку чтобы продолжить"
        await message.answer(
            f"С возвращением, {first_name}!\n\n{status}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Открыть панель", callback_data="adm:menu")],
                [InlineKeyboardButton(text="💳 Подписка", callback_data="billing:menu")],
            ])
        )
        return

    await message.answer(
        f"Привет, {first_name}! 👋\n\n"
        f"Я помогу настроить персонального AI-ассистента прямо в твоём Telegram.\n\n"
        f"Он будет отвечать на входящие сообщения от твоего имени — "
        f"пока ты занят, в дороге или просто хочешь делегировать рутину.\n\n"
        f"Подключается через официальную функцию Telegram "
        f"<b>«Автоматизация чатов»</b> — никаких серых схем.\n\n"
        f"Давай настроим всё за пару минут?\n\n"
        f"Для начала — как ты планируешь использовать ассистента?",
        reply_markup=kb_use_case()
    )
    logger.info(f'[ONBOARD] user_id={message.from_user.id} started onboarding')
    await log_event('onboard_start', message.from_user.id)
    await state.set_state(OnboardingFSM.step_use_case)


# ── Шаг 1 — Цель использования ───────────────────────────────────────────────

@router.callback_query(F.data.startswith("ob:usecase:"), OnboardingFSM.step_use_case)
async def ob_usecase(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    use_case = callback.data.split(":")[2]
    await state.update_data(use_case=use_case)
    logger.info(f'[ONBOARD] user_id={callback.from_user.id} use_case={use_case}')
    await update_user_setting(callback.from_user.id, "plan", use_case)

    await safe_edit(callback,
        "Теперь нужно подключить ассистента к твоему Telegram профилю.\n\n"
        "Открой Telegram:\n"
        "<b>Настройки → Автоматизация чатов → Добавить бота</b>\n\n"
        "Введи <code>@YourBotUsername</code> и выбери <b>«Все чаты»</b> — "
        "так ассистент сможет отвечать всем кто тебе пишет.\n\n"
        "Как подключишь — нажми кнопку ниже:",
        reply_markup=kb_connected()
    )
    await state.set_state(OnboardingFSM.step_connect)


# ── Шаг 2 — Подключение с реальной проверкой ─────────────────────────────────

@router.callback_query(F.data == "ob:connected", OnboardingFSM.step_connect)
async def ob_connected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id

    # Проверяем реальное подключение через БД
    connected = await is_connected(user_id)
    logger.info(f'[ONBOARD] user_id={user_id} check_connected={connected}')

    if not connected:
        await safe_edit(callback,
            "Похоже, бот ещё не подключён к твоему профилю 🤔\n\n"
            "Без этого ассистент не сможет видеть входящие сообщения "
            "и отвечать от твоего имени.\n\n"
            "Подключить просто:\n"
            "<b>Настройки → Автоматизация чатов → Добавить бота</b>\n"
            "Введи <code>@YourBotUsername</code>\n\n"
            "Как только подключишь — нажми кнопку ниже.\n"
            "Или пропусти пока — сможешь подключить в любое время через /help",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подключил, проверить снова", callback_data="ob:connected")],
                [InlineKeyboardButton(text="⏭ Пропустить, подключу позже", callback_data="ob:skip_connect")],
            ])
        )
        return

    await safe_edit(callback,
        "Отлично! Теперь настроим как ассистент будет отвечать от твоего имени.\n\n"
        "Как тебя зовут?\n\n"
        "<i>Имя нужно чтобы ассистент общался в нужном стиле "
        "и представлялся правильно.</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_name)


@router.callback_query(F.data == "ob:skip_connect", OnboardingFSM.step_connect)
async def ob_skip_connect(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_edit(callback,
        "Окей, подключишь позже через:\n"
        "<b>Настройки → Автоматизация чатов → Добавить бота</b>\n\n"
        "Пока настроим как ассистент будет отвечать.\n\n"
        "Как тебя зовут?\n\n"
        "<i>Имя нужно чтобы ассистент общался в нужном стиле.</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_name)


# ── Шаги 3-7 — Вопросы для промпта ───────────────────────────────────────────

@router.message(OnboardingFSM.step_name)
async def ob_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "Из какого вы города?\n\n"
        "<i>Напишите название на русском или английском — это нужно чтобы ассистент "
        "знал ваш часовой пояс и мог корректно работать с планировщиком сообщений.</i>"
    )
    await state.set_state(OnboardingFSM.step_city)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_name)
async def ob_skip_name(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(name="пользователь")
    await safe_edit(callback,
        "Из какого вы города?\n\n"
        "<i>Напишите название на русском или английском — это нужно чтобы ассистент "
        "знал ваш часовой пояс и мог корректно работать с планировщиком сообщений.</i>"
    )
    await state.set_state(OnboardingFSM.step_city)


# ── Шаг город / часовой пояс ─────────────────────────────────────────────────

async def _resolve_timezone(city: str) -> tuple[str | None, str | None]:
    """Определяет часовой пояс по названию города. Возвращает (timezone, city_display)."""
    try:
        from geopy.geocoders import Nominatim
        from timezonefinder import TimezoneFinder
        import asyncio

        loop = asyncio.get_event_loop()
        geolocator = Nominatim(user_agent="raveli_bot")

        location = await loop.run_in_executor(
            None, lambda: geolocator.geocode(city, language="ru", timeout=10)
        )
        if not location:
            return None, None

        tf = TimezoneFinder()
        tz = tf.timezone_at(lng=location.longitude, lat=location.latitude)
        return tz, location.address.split(",")[0].strip()
    except Exception:
        return None, None


@router.message(OnboardingFSM.step_city)
async def ob_city(message: Message, state: FSMContext):
    city_input = message.text.strip()
    await message.answer("Определяю часовой пояс...")

    tz, city_display = await _resolve_timezone(city_input)

    if not tz:
        await message.answer(
            "Не удалось найти такой город 🤔\n\n"
            "Попробуйте написать иначе — например <b>Moscow</b> вместо <b>Москва</b> "
            "или укажите страну: <b>Самарканд, Узбекистан</b>\n\n"
            "Напишите ещё раз:"
        )
        return  # остаёмся в том же стейте

    await set_user_timezone(message.from_user.id, tz)
    await state.update_data(city=city_display, timezone=tz)

    await message.answer(
        f"✅ Часовой пояс определён: <b>{tz}</b>\n"
        f"Город: <b>{city_display}</b>\n\n"
        "Чем занимаетесь? Пара слов — чтобы ассистент отвечал в правильном контексте.\n\n"
        "<i>Например: дизайнер-фрилансер / студент / менеджер по продажам</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_job)


@router.message(OnboardingFSM.step_job)
async def ob_job(message: Message, state: FSMContext):
    await state.update_data(job=message.text.strip())
    await message.answer(
        "Как ты обычно пишешь в переписках?",
        reply_markup=kb_style()
    )
    await state.set_state(OnboardingFSM.step_style)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_job)
async def ob_skip_job(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(job="")
    await safe_edit(callback,
        "Как ты обычно пишешь в переписках?",
        reply_markup=kb_style()
    )
    await state.set_state(OnboardingFSM.step_style)


@router.callback_query(F.data.startswith("ob:style:"), OnboardingFSM.step_style)
async def ob_style(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    style = callback.data.split(":")[2]

    if style == "custom":
        await safe_edit(callback, "Напиши как ты обычно общаешься в переписках:")
        await state.update_data(style="custom")
        return

    await state.update_data(style=style)
    await safe_edit(callback,
        "Что отвечать на спам и рекламные сообщения?\n\n"
        "<i>Например: игнорировать / ответить «не интересует» / ответить «спам»</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_spam)


@router.message(OnboardingFSM.step_style)
async def ob_style_custom(message: Message, state: FSMContext):
    await state.update_data(style="custom", style_custom=message.text.strip())
    await message.answer(
        "Что отвечать на спам и рекламные сообщения?\n\n"
        "<i>Например: игнорировать / ответить «не интересует»</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_spam)


@router.message(OnboardingFSM.step_spam)
async def ob_spam(message: Message, state: FSMContext):
    await state.update_data(spam=message.text.strip())
    await message.answer(
        "Последний вопрос — есть темы или вопросы которых лучше избегать?\n\n"
        "<i>Например: личные отношения / финансы / политика</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_avoid)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_spam)
async def ob_skip_spam(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(spam="игнорировать или ответить «не интересует»")
    await safe_edit(callback,
        "Последний вопрос — есть темы которых лучше избегать?\n\n"
        "<i>Например: личные отношения / финансы / политика</i>",
        reply_markup=kb_skip()
    )
    await state.set_state(OnboardingFSM.step_avoid)


@router.message(OnboardingFSM.step_avoid)
async def ob_avoid(message: Message, state: FSMContext):
    await state.update_data(avoid=message.text.strip())
    await _show_prompt_preview(message, state, via_message=True)


@router.callback_query(F.data == "ob:skip_step", OnboardingFSM.step_avoid)
async def ob_skip_avoid(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(avoid="")
    await _show_prompt_preview(callback.message, state, via_message=False, callback=callback)


async def _show_prompt_preview(message: Message, state: FSMContext,
                                via_message: bool = True, callback=None):
    data = await state.get_data()
    use_case = data.get("use_case", "personal")
    prompt = build_prompt(data, use_case)
    await state.update_data(built_prompt=prompt)

    text = (
        "✅ Готово! Вот что я составил:\n\n"
        f"<code>{prompt[:800]}{'…' if len(prompt) > 800 else ''}</code>\n\n"
        "Запустить с этим промптом или изменить?"
    )

    if via_message:
        await message.answer(text, reply_markup=kb_confirm())
    else:
        await safe_edit(callback, text, reply_markup=kb_confirm())

    await state.set_state(OnboardingFSM.step_confirm)


# ── Шаг 8 — Подтверждение ────────────────────────────────────────────────────

@router.callback_query(F.data == "ob:confirm")
async def ob_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    prompt = data.get("built_prompt", "")
    user_id = callback.from_user.id

    if not prompt:
        await callback.answer("Что-то пошло не так, попробуй /start заново", show_alert=True)
        return

    await update_user_setting(user_id, "system_prompt", prompt)
    await state.clear()
    logger.info(f'[ONBOARD] user_id={user_id} onboarding complete, prompt saved ({len(prompt)} chars)')
    await log_event('onboard_complete', user_id)

    trial_days = await get_trial_days_left(user_id)

    await safe_edit(callback,
        f"🚀 <b>Ассистент настроен!</b>\n\n"
        f"Осталось включить автоответ — и он начнёт работать.\n\n"
        f"У тебя есть <b>{trial_days} дней</b> бесплатного доступа — "
        f"этого хватит чтобы всё как следует попробовать.\n\n"
        f"Управляй через панель: включай/выключай, меняй промпт, "
        f"добавляй заметки о контактах.",
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
        "✏️ Напиши новый промпт — я сохраню его.\n\nОтмена → /start"
    )


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help_connect(message: Message):
    await message.answer(
        "📖 <b>Как подключить ассистента к профилю</b>\n\n"
        "<b>1.</b> Открой Telegram\n"
        "<b>2.</b> Настройки → Автоматизация чатов\n"
        "<b>3.</b> Добавь бота: <code>@YourBotUsername</code>\n"
        "<b>4.</b> Выбери к каким чатам он имеет доступ\n\n"
        "После подключения включи автоответ через /admin",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Открыть панель", callback_data="adm:menu")]
        ])
    )
