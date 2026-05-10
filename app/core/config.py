import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables
base_dir = Path(__file__).resolve().parent.parent.parent
dotenv_path = base_dir / ".env"
load_dotenv(dotenv_path=dotenv_path)

class Settings:
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-secret")
    ALGORITHM: str = "HS256"
    # Set to 0 to create non-expiring tokens; positive integer means minutes
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "0"))

    # SMTP
    SMTP_SERVER: Optional[str] = os.getenv("SMTP_SERVER")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: Optional[str] = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD: Optional[str] = os.getenv("SMTP_PASSWORD")
    SMTP_FROM: Optional[str] = os.getenv("SMTP_FROM", SMTP_USERNAME)
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/callback")

    # Cloudinary (if needed)
    CLOUDINARY_CLOUD_NAME: Optional[str] = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY: Optional[str] = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET: Optional[str] = os.getenv("CLOUDINARY_API_SECRET")

    # ByteDance Doubao
    DOUBAO_API_KEY: Optional[str] = os.getenv("DOUBAO_API_KEY")

    # Alibaba Cloud OSS
    OSS_ACCESS_KEY_ID: Optional[str] = os.getenv("OSS_ACCESS_KEY_ID")
    OSS_ACCESS_KEY_SECRET: Optional[str] = os.getenv("OSS_ACCESS_KEY_SECRET")
    OSS_ENDPOINT: Optional[str] = os.getenv("OSS_ENDPOINT")
    OSS_BUCKET: Optional[str] = os.getenv("OSS_BUCKET")
    OSS_BASE_URL: Optional[str] = os.getenv("OSS_BASE_URL")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info").upper()

    # Default avatar URLs
    DEFAULT_AVATAR_URLS = [
        "https://api.dicebear.com/6.x/bottts/svg?seed=cyber-1",
        "https://api.dicebear.com/6.x/bottts/svg?seed=cyber-2",
        "https://api.dicebear.com/6.x/bottts/svg?seed=cyber-3",
        "https://api.dicebear.com/6.x/bottts/svg?seed=cyber-4",
        "https://api.dicebear.com/6.x/bottts/svg?seed=cyber-5",
    ]

    def oss_ready(self) -> bool:
        return all([
            self.OSS_ACCESS_KEY_ID,
            self.OSS_ACCESS_KEY_SECRET,
            self.OSS_ENDPOINT,
            self.OSS_BUCKET,
            self.OSS_BASE_URL,
        ])

    def cloudinary_ready(self) -> bool:
        return all([
            self.CLOUDINARY_CLOUD_NAME,
            self.CLOUDINARY_API_KEY,
            self.CLOUDINARY_API_SECRET,
        ])

settings = Settings()