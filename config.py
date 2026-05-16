import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))  # только для /owneradmin

LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-v4-flash")

DB_PATH: str = os.getenv("DB_PATH", "bot.db")
MAX_HISTORY: int = int(os.getenv("MAX_HISTORY", "12"))

SYSTEM_PROMPT_DEFAULT: str = os.getenv(
    "SYSTEM_PROMPT_DEFAULT",
    "You are a personal AI assistant. Reply to messages on behalf of the user. Never reveal you are an AI."
)
