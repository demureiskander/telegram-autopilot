import aiosqlite
from datetime import datetime, timedelta
from config import DB_PATH, SYSTEM_PROMPT_DEFAULT

TRIAL_DAYS = 16

PLANS = {
    "personal": {
        "daily_messages": 200,
        "max_input_tokens": 2000,
        "max_output_tokens": 1000,
        "rpm": 3,
    },
    "business": {
        "daily_messages": 500,
        "max_input_tokens": 4000,
        "max_output_tokens": 2000,
        "rpm": 5,
    },
}

PRICES = {
    "personal": {"week": 150, "month": 450, "quarter": 1100, "year": 3600},
    "business": {"week": 400, "month": 1200, "quarter": 2900, "year": 9600},
}

PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 90, "year": 365}

PERIOD_LABELS = {"week": "7 дней", "month": "1 месяц", "quarter": "3 месяца", "year": "1 год"}


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id            INTEGER PRIMARY KEY,
                username           TEXT,
                first_name         TEXT,
                trial_started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                subscription_until TIMESTAMP,
                plan               TEXT DEFAULT 'personal',
                is_enabled         INTEGER DEFAULT 0,
                is_connected       INTEGER DEFAULT 0,
                active_model       TEXT DEFAULT 'deepseek-v4-flash',
                system_prompt      TEXT,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                user_id     INTEGER NOT NULL,
                chat_id     INTEGER NOT NULL,
                name        TEXT,
                msg_count   INTEGER DEFAULT 0,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS contact_notes (
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                user_note  TEXT DEFAULT '',
                ai_note    TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id  INTEGER NOT NULL,
                date     TEXT    NOT NULL,
                count    INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as c:
            row = await c.fetchone()
            return dict(row) if row else None


async def register_user(user_id: int, username: str, first_name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, system_prompt)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, SYSTEM_PROMPT_DEFAULT))
        await db.commit()


async def is_access_allowed(user_id: int) -> tuple[bool, str]:
    user = await get_user(user_id)
    if not user:
        return False, "not_registered"
    now = datetime.utcnow()
    if user["subscription_until"]:
        sub_until = datetime.fromisoformat(user["subscription_until"])
        if sub_until > now:
            return True, "subscription"
    trial_started = datetime.fromisoformat(user["trial_started_at"])
    trial_until = trial_started + timedelta(days=TRIAL_DAYS)
    if trial_until > now:
        return True, "trial"
    return False, "expired"


async def get_trial_days_left(user_id: int) -> int:
    user = await get_user(user_id)
    if not user:
        return 0
    trial_started = datetime.fromisoformat(user["trial_started_at"])
    trial_until = trial_started + timedelta(days=TRIAL_DAYS)
    delta = trial_until - datetime.utcnow()
    return max(0, delta.days)


async def get_subscription_days_left(user_id: int) -> int:
    user = await get_user(user_id)
    if not user or not user["subscription_until"]:
        return 0
    sub_until = datetime.fromisoformat(user["subscription_until"])
    delta = sub_until - datetime.utcnow()
    return max(0, delta.days)


async def extend_subscription(user_id: int, period: str, plan: str) -> None:
    days = PERIOD_DAYS[period]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT subscription_until FROM users WHERE user_id = ?", (user_id,)
        ) as c:
            row = await c.fetchone()
        now = datetime.utcnow()
        if row and row[0]:
            current = datetime.fromisoformat(row[0])
            base = max(current, now)
        else:
            base = now
        new_until = base + timedelta(days=days)
        await db.execute("""
            UPDATE users SET subscription_until = ?, plan = ? WHERE user_id = ?
        """, (new_until.isoformat(), plan, user_id))
        await db.commit()


async def update_user_setting(user_id: int, key: str, value) -> None:
    allowed = {"is_enabled", "active_model", "system_prompt", "plan"}
    if key not in allowed:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE users SET {key} = ? WHERE user_id = ?", (value, user_id)
        )
        await db.commit()


async def check_daily_limit(user_id: int, plan: str) -> tuple[bool, int, int]:
    limit = PLANS[plan]["daily_messages"]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND date = ?",
            (user_id, today)
        ) as c:
            row = await c.fetchone()
            used = row[0] if row else 0
    return used < limit, used, limit


async def increment_daily_usage(user_id: int) -> None:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO daily_usage (user_id, date, count) VALUES (?, ?, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1
        """, (user_id, today))
        await db.commit()


async def get_history(user_id: int, chat_id: int, limit: int = 12) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT role, content FROM messages
            WHERE user_id = ? AND chat_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, chat_id, limit)) as c:
            rows = await c.fetchall()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


async def save_message(user_id: int, chat_id: int, role: str, content: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO messages (user_id, chat_id, role, content)
            VALUES (?, ?, ?, ?)
        """, (user_id, chat_id, role, content))
        await db.commit()


async def upsert_chat(user_id: int, chat_id: int, name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chats (user_id, chat_id, name, msg_count, last_active)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                name = excluded.name,
                msg_count = msg_count + 1,
                last_active = CURRENT_TIMESTAMP
        """, (user_id, chat_id, name))
        await db.commit()


async def clear_history(user_id: int, chat_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM messages WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await db.commit()


async def clear_all_history(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM messages WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def get_stats(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM messages WHERE user_id = ?", (user_id,)
        ) as c:
            total_chats = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND role='user'", (user_id,)
        ) as c:
            total_incoming = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND role='assistant'", (user_id,)
        ) as c:
            total_replies = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM messages
            WHERE user_id = ? AND role='user' AND DATE(created_at) = DATE('now')
        """, (user_id,)) as c:
            today_msgs = (await c.fetchone())[0]
    return {
        "total_chats": total_chats,
        "total_incoming": total_incoming,
        "total_replies": total_replies,
        "today_msgs": today_msgs,
    }


async def get_chat_list(user_id: int, limit: int = 100) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT c.chat_id, c.name, c.msg_count, c.last_active,
                   (SELECT content FROM messages
                    WHERE user_id = c.user_id AND chat_id = c.chat_id
                    AND role = 'user' ORDER BY created_at DESC LIMIT 1) as last_msg,
                   (SELECT note FROM contact_notes
                    WHERE user_id = c.user_id AND chat_id = c.chat_id) as note
            FROM chats c WHERE c.user_id = ?
            ORDER BY c.last_active DESC LIMIT ?
        """, (user_id, limit)) as c:
            return await c.fetchall()


async def get_chat_messages(user_id: int, chat_id: int, limit: int = 8) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT role, content, created_at FROM messages
            WHERE user_id = ? AND chat_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, chat_id, limit)) as c:
            rows = await c.fetchall()
            return list(reversed(rows))


async def get_notes(user_id: int, chat_id: int) -> dict:
    """Возвращает обе заметки: пользовательскую и от ИИ."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_note, ai_note FROM contact_notes WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        ) as c:
            row = await c.fetchone()
            if row:
                return {"user_note": row[0] or "", "ai_note": row[1] or ""}
            return {"user_note": "", "ai_note": ""}


async def get_note(user_id: int, chat_id: int) -> str:
    """Возвращает объединённую заметку для LLM."""
    notes = await get_notes(user_id, chat_id)
    parts = []
    if notes["user_note"]:
        parts.append(notes["user_note"])
    if notes["ai_note"]:
        parts.append(notes["ai_note"])
    return " | ".join(parts) if parts else ""


async def set_user_note(user_id: int, chat_id: int, note: str) -> None:
    """Пользователь редактирует свою заметку."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO contact_notes (user_id, chat_id, user_note, ai_note, updated_at)
            VALUES (?, ?, ?, '', CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                user_note  = excluded.user_note,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, chat_id, note))
        await db.commit()


async def set_note(user_id: int, chat_id: int, note: str) -> None:
    """ИИ обновляет свою заметку автоматически."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO contact_notes (user_id, chat_id, user_note, ai_note, updated_at)
            VALUES (?, ?, '', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                ai_note    = excluded.ai_note,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, chat_id, note))
        await db.commit()


async def delete_user_note(user_id: int, chat_id: int) -> None:
    """Очищает только пользовательскую заметку."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE contact_notes SET user_note = '' WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await db.commit()


async def delete_note(user_id: int, chat_id: int) -> None:
    """Очищает обе заметки."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM contact_notes WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        await db.commit()


async def get_global_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM users
            WHERE subscription_until > datetime('now')
        """) as c:
            paid_users = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM users
            WHERE trial_started_at >= datetime('now', '-16 days')
            AND (subscription_until IS NULL OR subscription_until <= datetime('now'))
        """) as c:
            trial_users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM messages") as c:
            total_messages = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM users WHERE DATE(created_at) = DATE('now')
        """) as c:
            new_today = (await c.fetchone())[0]
    return {
        "total_users": total_users,
        "paid_users": paid_users,
        "trial_users": trial_users,
        "total_messages": total_messages,
        "new_today": new_today,
    }


async def set_connected(user_id: int, connected: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_connected = ? WHERE user_id = ?",
            (1 if connected else 0, user_id)
        )
        await db.commit()


async def is_connected(user_id: int) -> bool:
    user = await get_user(user_id)
    return bool(user and user.get("is_connected"))
