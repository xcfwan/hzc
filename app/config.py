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
    qb_url: str = os.getenv("QB_URL", "")
    qb_username: str = os.getenv("QB_USERNAME", "")
    qb_password: str = os.getenv("QB_PASSWORD", "")
    qb_store_path: str = os.getenv("QB_STORE_PATH", "/app/state/qb_nodes.json")
    app_version: str = os.getenv("APP_VERSION", "26.3.13")
    runtime_config_path: str = os.getenv("RUNTIME_CONFIG_PATH", "/app/state/runtime_config.json")
    auto_policy_path: str = os.getenv("AUTO_POLICY_PATH", "/app/state/auto_policies.json")


settings = Settings()
