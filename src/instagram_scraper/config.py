import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass(slots=True)
class Config:
    ig_username: Optional[str] = None
    ig_password: Optional[str] = None
    ig_2fa_code: Optional[str] = None
    posts_limit: int = 5
    # Facebook OAuth
    fb_email: Optional[str] = None
    fb_password: Optional[str] = None
    fb_2fa_code: Optional[str] = None
    # Headless browser and storage
    headless: bool = True
    storage_path: str = "storage/auth_state.enc"
    storage_plain_path: str = "storage/storage_state.json"
    auth_secret_key: Optional[str] = None
    log_level: str = "INFO"


def load_config() -> Config:
    """Carga variables de entorno desde .env y devuelve la Config."""
    load_dotenv()
    return Config(
        ig_username=os.getenv("IG_USERNAME"),
        ig_password=os.getenv("IG_PASSWORD"),
        ig_2fa_code=os.getenv("IG_2FA_CODE"),
        posts_limit=int(os.getenv("POSTS_LIMIT", "5")),
        fb_email=os.getenv("FB_EMAIL"),
        fb_password=os.getenv("FB_PASSWORD"),
        fb_2fa_code=os.getenv("FB_2FA_CODE"),
        headless=os.getenv("HEADLESS", "true").lower() == "true",
        storage_path=os.getenv("AUTH_STORAGE_PATH", "storage/auth_state.enc"),
        storage_plain_path=os.getenv("AUTH_STORAGE_PLAIN_PATH", "storage/storage_state.json"),
        auth_secret_key=os.getenv("AUTH_SECRET_KEY"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )