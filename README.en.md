<img width="1915" height="530" alt="Frame 188" src="https://github.com/user-attachments/assets/89fa6d95-d67d-4c65-8b6f-cf490d7b257b" />

# 🤖 telegram-autopilot — cloud

🇷🇺 [Русский](README.md) · 🇬🇧 English

> **Try it now → [@RavelioBot](https://t.me/RavelioBot)**

A personal AI assistant built into your Telegram profile.  
It reads incoming messages and replies on your behalf — when you're busy, on the go, or just want to delegate routine conversations.

Connects via the official Telegram **Chat Automation** feature — no grey-area workarounds, only the official Bot API.

---

> 🔧 Want to run it yourself?  
> Single-user self-hosted version → [telegram-autopilot](https://github.com/demureiskander/telegram-autopilot)

---

## ✨ Features

- 💬 **Replies in your style** — you define the tone, phrases and personality via system prompt
- 🔘 **One-tap on/off** — enable when busy, disable when you're back
- 📝 **Two types of notes** — your notes about a contact + AI notes updated automatically
- 🧠 **Smart context** — the assistant knows who's writing, how many times you've talked, what's known about them
- 🎭 **Tone detection** — adapts between formal and informal style
- 📊 **Control panel** — stats, chat history, prompt editor, model selector
- 🔁 **Auto model fallback** — if the primary model is unavailable, switches to the next automatically
- 🔌 **Works with any OpenAI-compatible API** — OpenRouter, DeepSeek, NeuroAPI and others

---

## 💳 Pricing

| | Personal | Business |
|---|---|---|
| Messages per day | 200 | 500 |
| Input tokens per message | 2 000 | 4 000 |
| Output tokens per message | 1 000 | 2 000 |
| Week | 150 ⭐ | 400 ⭐ |
| Month | 450 ⭐ | 1 200 ⭐ |
| 3 months | 1 100 ⭐ | 2 900 ⭐ |
| Year | 3 600 ⭐ | 9 600 ⭐ |

New users get a **16-day free trial**.

---

## 🚀 Quick Start (self-hosted cloud)

### Requirements

- Python 3.12
- Telegram bot with Business Mode enabled
- API key from any OpenAI-compatible provider
- A server or Railway for hosting

### 1 — Clone the repository

```bash
git clone https://github.com/demureiskander/telegram-autopilot.git
cd telegram-autopilot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2 — Create a bot

1. [@BotFather](https://t.me/BotFather) → `/newbot`
2. `/mybots` → Bot Settings → **Business Mode** → Turn on
3. Copy the token

### 3 — Configure .env

```bash
cp .env.example .env
```

```env
BOT_TOKEN=your_bot_token
OWNER_ID=your_telegram_id       # get it from @userinfobot
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://openrouter.ai/api/v1/chat/completions
LLM_MODEL=deepseek/deepseek-chat-v3-0324:free
DB_PATH=bot.db
MAX_HISTORY=12
BOT_USERNAME=@YourBotUsername   # replace with your bot's username
```

> ⚠️ `OWNER_ID` grants access to `/owneradmin` — only for you as the owner.

### 4 — Replace @YourBotUsername

```bash
grep -rn "@YourBotUsername" handlers/
```

### 5 — Run

```bash
python bot.py
```

---

## ☁️ Deploy to Railway

1. Push the repository to GitHub
2. [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add all variables from `.env` to **Variables**
4. Add a **Volume** → mount path `/app/data`
5. Add variable `DB_PATH=/app/data/bot.db`

Railway redeploys automatically on every push to `main`.

---

## 🛠 Control Panel

The `/admin` command opens the personal control panel:

| Button | What it does |
|---|---|
| ✅/⏸ Enable/Disable | Toggles auto-reply on or off |
| 📊 Stats | Messages today, unique chats |
| 💬 Chats | List of conversations with notes and history |
| ✏️ Prompt | View and edit the system prompt |
| 🤖 Model | Choose the LLM model |
| 🗑 History | Clear chat history |
| 💳 Subscription | Pricing and payment |

### Quick Commands

| Command | Action |
|---|---|
| `/start` | Start / return to setup |
| `/admin` | Open control panel |
| `/on` | Enable auto-reply |
| `/off` | Disable auto-reply |
| `/status` | Current status |
| `/help` | How to connect to profile |

---

## 👑 Owner Panel

The `/owneradmin` command — only for `OWNER_ID`:

- **Stats** — onboarding funnel, subscriptions by plan, growth
- **Activity** — DAU/WAU/MAU, messages per day, LLM errors, model usage
- **Finance** — Stars earned, Stars→USD, transactions by plan
- **Users** — paginated user list
- **Database** — export `.db.gz`
- **Cleanup** — delete events older than 30/90 days

```
/banuser ID      — ban a user
/unbanuser ID    — unban a user
/deleteuser ID   — delete all user data
```

---

## 🧠 How Context Works

Every LLM request contains three blocks:

```
=== ASSISTANT ROLE ===
AI assistant embedded in a Telegram profile...

=== ABOUT THE PROFILE OWNER ===
[system prompt configured by the user]

=== WHO IS WRITING NOW ===
Name: ...  Username: @...
[user note + AI note if available]
[first message / message #N]
```

### Automatic Notes

After each message, a background task analyses the conversation. If it learns something new about the contact — it updates the note automatically. Users can also add their own note separately.

---

## 📁 Project Structure

```
telegram-autopilot/
├── bot.py
├── config.py
├── logger.py
├── requirements.txt
├── handlers/
│   ├── start.py       # Onboarding (8 steps)
│   ├── admin.py       # User control panel
│   ├── billing.py     # Pricing and Stars payment
│   ├── business.py    # Incoming message handler
│   └── owner.py       # Owner panel
├── middlewares/
│   └── access.py
├── services/
│   └── llm.py         # AI API with fallback
└── database/
    └── db.py
```

---

## 🔒 Privacy

Logs contain only metadata — user_id, chat_id, model, timestamp. Message content is **never logged** at the server level.

---

## 📄 License

[Sustainable Use License](LICENSE) — free for personal use and self-hosted deployment. Commercial distribution as a service requires a separate agreement with the author.
