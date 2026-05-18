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
                is_banned          INTEGER DEFAULT 0,
                timezone           TEXT DEFAULT 'UTC',
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                user_id    INTEGER,
                meta       TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                plan       TEXT NOT NULL,
                period     TEXT NOT NULL,
                stars      INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id              INTEGER NOT NULL,
                chat_id               INTEGER NOT NULL,
                text                  TEXT NOT NULL,
                send_at               TIMESTAMP NOT NULL,
                business_connection_id TEXT,
                status                TEXT DEFAULT 'pending',
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_send_at ON scheduled_messages(send_at, status)"
        )

        # Миграции — добавляем колонки если не существуют
        for migration in [
            "ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'UTC'",
            "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_connected INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN business_connection_id TEXT DEFAULT ''",
            "ALTER TABLE contact_notes ADD COLUMN user_note TEXT DEFAULT ''",
            "ALTER TABLE contact_notes ADD COLUMN ai_note TEXT DEFAULT ''",
            "ALTER TABLE contact_notes ADD COLUMN note TEXT DEFAULT ''",
        ]:
            try:
                await db.execute(migration)
            except Exception:
                pass

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
                   (SELECT user_note FROM contact_notes
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


# ── Events / Аналитика ────────────────────────────────────────────────────────

async def log_event(event_type: str, user_id: int | None = None, meta: str = "") -> None:
    """Логируем действие без контента сообщений."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO events (event_type, user_id, meta) VALUES (?, ?, ?)",
                (event_type, user_id, meta)
            )
            await db.commit()
    except Exception:
        pass


async def log_payment(user_id: int, plan: str, period: str, stars: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id, plan, period, stars) VALUES (?, ?, ?, ?)",
            (user_id, plan, period, stars)
        )
        await db.commit()


async def get_owner_stats() -> dict:
    """Полная статистика для владельца."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Пользователи
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE subscription_until > datetime('now')"
        ) as c:
            paid_users = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM users
            WHERE trial_started_at >= datetime('now', '-16 days')
            AND (subscription_until IS NULL OR subscription_until <= datetime('now'))
        """) as c:
            trial_users = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM users
            WHERE (subscription_until IS NULL OR subscription_until <= datetime('now'))
            AND trial_started_at < datetime('now', '-16 days')
        """) as c:
            expired_users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_connected=1") as c:
            connected_users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_enabled=1") as c:
            active_users = (await c.fetchone())[0]

        # Онбординг воронка
        async with db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='onboard_start'"
        ) as c:
            onboard_started = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='onboard_complete'"
        ) as c:
            onboard_done = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='connect_profile'"
        ) as c:
            connected_ever = (await c.fetchone())[0]

        # DAU/WAU/MAU — по событиям
        async with db.execute("""
            SELECT COUNT(DISTINCT user_id) FROM events
            WHERE DATE(created_at) = DATE('now') AND user_id IS NOT NULL
        """) as c:
            dau = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(DISTINCT user_id) FROM events
            WHERE created_at >= datetime('now', '-7 days') AND user_id IS NOT NULL
        """) as c:
            wau = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(DISTINCT user_id) FROM events
            WHERE created_at >= datetime('now', '-30 days') AND user_id IS NOT NULL
        """) as c:
            mau = (await c.fetchone())[0]

        # Прирост
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(created_at) = DATE('now')"
        ) as c:
            new_today = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')"
        ) as c:
            new_week = (await c.fetchone())[0]

        # Сообщения
        async with db.execute("SELECT COUNT(*) FROM events WHERE event_type='message_handled'") as c:
            total_messages = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(*) FROM events
            WHERE event_type='message_handled' AND DATE(created_at) = DATE('now')
        """) as c:
            messages_today = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='llm_error'"
        ) as c:
            llm_errors = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='llm_fallback'"
        ) as c:
            llm_fallbacks = (await c.fetchone())[0]

        # Финансы
        async with db.execute("SELECT COALESCE(SUM(stars), 0) FROM payments") as c:
            total_stars = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(SUM(stars), 0) FROM payments WHERE DATE(created_at) = DATE('now')"
        ) as c:
            stars_today = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(SUM(stars), 0) FROM payments WHERE created_at >= datetime('now', '-30 days')"
        ) as c:
            stars_month = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM payments") as c:
            total_payments = (await c.fetchone())[0]

        # По планам
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE plan='personal' AND subscription_until > datetime('now')"
        ) as c:
            personal_paid = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE plan='business' AND subscription_until > datetime('now')"
        ) as c:
            business_paid = (await c.fetchone())[0]

        # Активность по моделям
        async with db.execute("""
            SELECT meta, COUNT(*) as cnt FROM events
            WHERE event_type='message_handled' AND meta != ''
            GROUP BY meta ORDER BY cnt DESC LIMIT 5
        """) as c:
            model_stats = await c.fetchall()

        # Динамика за 7 дней
        async with db.execute("""
            SELECT DATE(created_at) as d, COUNT(*) as cnt
            FROM events WHERE event_type='message_handled'
            AND created_at >= datetime('now', '-7 days')
            GROUP BY d ORDER BY d
        """) as c:
            daily_msgs = await c.fetchall()

    def pct(n, total):
        if not total:
            return "—"
        return f"{round(n / total * 100)}%"

    return {
        "total_users": total_users,
        "paid_users": paid_users,
        "trial_users": trial_users,
        "expired_users": expired_users,
        "connected_users": connected_users,
        "active_users": active_users,
        "onboard_started": onboard_started,
        "onboard_done": onboard_done,
        "connected_ever": connected_ever,
        "onboard_conv": pct(onboard_done, onboard_started),
        "connect_conv": pct(connected_ever, onboard_started),
        "dau": dau, "wau": wau, "mau": mau,
        "new_today": new_today, "new_week": new_week,
        "total_messages": total_messages,
        "messages_today": messages_today,
        "llm_errors": llm_errors,
        "llm_fallbacks": llm_fallbacks,
        "total_stars": total_stars,
        "stars_today": stars_today,
        "stars_month": stars_month,
        "total_payments": total_payments,
        "personal_paid": personal_paid,
        "business_paid": business_paid,
        "model_stats": model_stats,
        "daily_msgs": daily_msgs,
        "pct_fn": pct,
    }


async def get_users_list(limit: int = 20, offset: int = 0) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT user_id, username, first_name, plan, is_enabled, is_connected,
                   trial_started_at, subscription_until, created_at
            FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (limit, offset)) as c:
            return await c.fetchall()


async def ban_user(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def unban_user(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def delete_user_data(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM contact_notes WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM daily_usage WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
        await db.commit()


async def clean_old_events(days: int = 90) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT COUNT(*) FROM events WHERE created_at < datetime('now', '-{days} days')"
        ) as c:
            count = (await c.fetchone())[0]
        await db.execute(
            f"DELETE FROM events WHERE created_at < datetime('now', '-{days} days')"
        )
        await db.commit()
    return count


async def set_user_timezone(user_id: int, timezone: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?",
            (timezone, user_id)
        )
        await db.commit()


# ── Планировщик сообщений ─────────────────────────────────────────────────────

async def add_scheduled_message(
    owner_id: int,
    chat_id: int,
    text: str,
    send_at_utc: str,
    business_connection_id: str = "",
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO scheduled_messages
            (owner_id, chat_id, text, send_at, business_connection_id)
            VALUES (?, ?, ?, ?, ?)
        """, (owner_id, chat_id, text, send_at_utc, business_connection_id))
        await db.commit()
        return cursor.lastrowid


async def get_pending_scheduled(limit: int = 50) -> list:
    """Возвращает сообщения готовые к отправке."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, owner_id, chat_id, text, business_connection_id
            FROM scheduled_messages
            WHERE status = 'pending'
            AND send_at <= datetime('now')
            ORDER BY send_at ASC
            LIMIT ?
        """, (limit,)) as c:
            return await c.fetchall()


async def mark_scheduled_sent(msg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_messages SET status = 'sent' WHERE id = ?",
            (msg_id,)
        )
        await db.commit()


async def mark_scheduled_failed(msg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_messages SET status = 'failed' WHERE id = ?",
            (msg_id,)
        )
        await db.commit()


async def get_user_scheduled(owner_id: int, limit: int = 10) -> list:
    """Список запланированных сообщений пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, chat_id, text, send_at, status
            FROM scheduled_messages
            WHERE owner_id = ? AND status = 'pending'
            ORDER BY send_at ASC
            LIMIT ?
        """, (owner_id, limit)) as c:
            return await c.fetchall()


async def cancel_scheduled(msg_id: int, owner_id: int) -> bool:
    """Отменяет запланированное сообщение. Возвращает True если удалось."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            UPDATE scheduled_messages SET status = 'cancelled'
            WHERE id = ? AND owner_id = ? AND status = 'pending'
        """, (msg_id, owner_id))
        await db.commit()
        return cursor.rowcount > 0


async def save_business_connection_id(user_id: int, connection_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET business_connection_id = ? WHERE user_id = ?",
            (connection_id, user_id)
        )
        await db.commit()


async def get_today_messages(user_id: int, chat_id: int) -> list[dict]:
    """Возвращает сообщения за сегодня для сводки."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT role, content, created_at FROM messages
            WHERE user_id = ? AND chat_id = ?
            AND DATE(created_at) = DATE('now')
            ORDER BY created_at ASC
        """, (user_id, chat_id)) as c:
            rows = await c.fetchall()
            return [{"role": r[0], "content": r[1], "time": r[2][11:16]} for r in rows]
