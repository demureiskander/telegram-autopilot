from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery,
)
from aiogram.exceptions import TelegramBadRequest

from logger import logger
from database.db import (
    get_user, extend_subscription, get_subscription_days_left,
    get_trial_days_left, PRICES, PERIOD_LABELS, TRIAL_DAYS,
)

router = Router()


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def kb_billing_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Personal", callback_data="billing:plan:personal"),
            InlineKeyboardButton(text="🏢 Business", callback_data="billing:plan:business"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")],
    ])


def kb_periods(plan: str) -> InlineKeyboardMarkup:
    prices = PRICES[plan]
    buttons = []
    for period, stars in prices.items():
        label = PERIOD_LABELS[period]
        buttons.append([InlineKeyboardButton(
            text=f"{label} — {stars} ⭐",
            callback_data=f"billing:pay:{plan}:{period}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="billing:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "billing:menu")
async def on_billing_menu(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    plan = user["plan"] if user else "personal"
    trial_days = await get_trial_days_left(user_id)
    sub_days = await get_subscription_days_left(user_id)

    if sub_days > 0:
        status = f"✅ Подписка активна ещё <b>{sub_days} дн.</b> · тариф <b>{'Personal' if plan == 'personal' else 'Business'}</b>"
    elif trial_days > 0:
        status = f"🎁 Триал: осталось <b>{trial_days} из {TRIAL_DAYS} дней</b>"
    else:
        status = "⚠️ Доступ истёк — выбери тариф чтобы продолжить"

    await safe_edit(callback,
        f"💳 <b>Тарифы и оплата</b>\n\n"
        f"{status}\n\n"
        f"👤 <b>Personal</b>\n"
        f"До 200 входящих сообщений в день\n"
        f"До 2000 токенов на входящее · до 1000 на ответ\n\n"
        f"🏢 <b>Business</b>\n"
        f"До 500 входящих сообщений в день\n"
        f"До 4000 токенов на входящее · до 2000 на ответ\n\n"
        f"<i>Токен ≈ ¾ слова. Лимит на сообщение — это максимальная длина "
        f"одного входящего текста и одного ответа.</i>\n\n"
        f"Выбери тариф:",
        reply_markup=kb_billing_menu()
    )


@router.callback_query(F.data.startswith("billing:plan:"))
async def on_billing_plan(callback: CallbackQuery):
    await callback.answer()
    plan = callback.data.split(":")[2]
    logger.info(f'[BILLING] user_id={callback.from_user.id} selected plan={plan}')
    plan_name = "👤 Personal" if plan == "personal" else "🏢 Business"

    await safe_edit(callback,
        f"💳 <b>{plan_name}</b>\n\nВыбери период подписки:",
        reply_markup=kb_periods(plan)
    )


@router.callback_query(F.data.startswith("billing:pay:"))
async def on_billing_pay(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    _, _, plan, period = callback.data.split(":")
    logger.info(f'[BILLING] user_id={callback.from_user.id} initiating payment plan={plan} period={period}')
    stars = PRICES[plan][period]
    label = PERIOD_LABELS[period]
    plan_name = "Personal" if plan == "personal" else "Business"

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"TgAutopilot {plan_name}",
        description=f"Подписка на {label}",
        payload=f"{plan}:{period}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{plan_name} · {label}", amount=stars)],
    )


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    plan, period = payload.split(":")
    user_id = message.from_user.id

    await extend_subscription(user_id, period, plan)
    logger.info(f'[BILLING] user_id={user_id} payment success plan={plan} period={period} stars={message.successful_payment.total_amount}')

    plan_name = "Personal" if plan == "personal" else "Business"

    await message.answer(
        f"✅ <b>Оплата прошла!</b>\n\n"
        f"Тариф: <b>{plan_name}</b>\n"
        f"Период: <b>{PERIOD_LABELS[period]}</b>\n\n"
        f"Доступ активирован. Приятного использования! 🎉",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Открыть панель", callback_data="adm:menu")]
        ])
    )
