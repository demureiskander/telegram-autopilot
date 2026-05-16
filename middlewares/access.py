from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery
from database.db import get_user, register_user
from logger import logger


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        from_user = None
        if hasattr(event, "message") and event.message:
            from_user = event.message.from_user
        elif hasattr(event, "callback_query") and event.callback_query:
            from_user = event.callback_query.from_user
        elif hasattr(event, "from_user"):
            from_user = event.from_user

        if not from_user:
            return await handler(event, data)

        user_id = from_user.id

        try:
            user = await get_user(user_id)
            if not user:
                await register_user(
                    user_id,
                    from_user.username or "",
                    from_user.first_name or "",
                )
                logger.info(f"[NEW_USER] user_id={user_id} username=@{from_user.username} name='{from_user.first_name}'")
            
            data["user_id"] = user_id

        except Exception as e:
            logger.error(f"[MIDDLEWARE] Error processing user_id={user_id}: {e}", exc_info=True)

        return await handler(event, data)
