from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()


class Settings(BaseModel):
    hetzner_token: str = os.getenv("HETZNER_TOKEN", "")
    traffic_limit_tb: float = float(os.getenv("TRAFFIC_LIMIT_TB", "20"))
    rotate_threshold: float = float(os.getenv("ROTATE_THRESHOLD", "0.9"))
    check_interval_minutes: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
    safe_mode: bool = os.getenv("SAFE_MODE", "true").lower() in ("1", "true", "yes", "on")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    timezone: str = os.getenv("TZ", "UTC")
    snapshot_price_per_gb: float = float(os.getenv("SNAPSHOT_PRICE_PER_GB", "0.011"))


settings = Settings()
