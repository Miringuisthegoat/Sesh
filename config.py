import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

INSECURE_DEV_KEY = "dev-only-insecure-key-do-not-use-in-production"


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        (BASE_DIR / "instance").mkdir(exist_ok=True)
        return "sqlite:///" + (BASE_DIR / "instance" / "sesh.db").as_posix()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Config:
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
    SECRET_KEY = os.environ.get("SECRET_KEY") or INSECURE_DEV_KEY
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessions / cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENVIRONMENT == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_int("SESSION_HOURS", 12))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = ENVIRONMENT == "production"
    WTF_CSRF_TIME_LIMIT = None  # CSRF token valid for the session lifetime

    # Rate limiting
    LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "10 per minute")
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    TRUSTED_PROXY_COUNT = _int("TRUSTED_PROXY_COUNT", 0)

    # Policy
    PASSWORD_MIN_LENGTH = _int("PASSWORD_MIN_LENGTH", 10)
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 25)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    ALLOWED_EXTENSIONS = tuple(e.strip().lower() for e in os.environ.get("ALLOWED_EXTENSIONS", "docx,xlsx,jpg,jpeg,png").split(","))
    RETENTION_DAYS_DEFAULT = _int("RETENTION_DAYS_DEFAULT", 90)
    MAX_COPIES_LIMIT = _int("MAX_COPIES_LIMIT", 20)  # hard ceiling for any company setting

    # MVP restrictions, enforced in services (not hard-coded in the schema)
    MAX_OFFICES_PER_COMPANY = _int("MAX_OFFICES_PER_COMPANY", 1)
    MAX_ACTIVE_AGENTS_PER_OFFICE = _int("MAX_ACTIVE_AGENTS_PER_OFFICE", 1)
    MAX_ACTIVE_PRINTERS_PER_OFFICE = _int("MAX_ACTIVE_PRINTERS_PER_OFFICE", 1)

    # Agent / jobs (used from later phases)
    AGENT_HEARTBEAT_SECONDS = _int("AGENT_HEARTBEAT_SECONDS", 30)
    AGENT_OFFLINE_AFTER_SECONDS = _int("AGENT_OFFLINE_AFTER_SECONDS", 90)
    JOB_LEASE_SECONDS = _int("JOB_LEASE_SECONDS", 120)
    MAX_CONVERSION_RETRIES = _int("MAX_CONVERSION_RETRIES", 3)

    # Storage / queue (used from Phase 3)
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    STORAGE_LOCAL_PATH = os.environ.get("STORAGE_LOCAL_PATH", "storage")
    R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "")
    R2_BUCKET = os.environ.get("R2_BUCKET", "")
    R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Documents / conversion (Phase 3)
    UPLOAD_RATE_LIMIT = os.environ.get("UPLOAD_RATE_LIMIT", "20 per minute")
    MAX_IMAGE_PIXELS = _int("MAX_IMAGE_PIXELS", 50_000_000)
    CONVERSION_LEASE_SECONDS = _int("CONVERSION_LEASE_SECONDS", 600)  # a PROCESSING attempt older than this is presumed lost
    WORKER_POLL_SECONDS = _int("WORKER_POLL_SECONDS", 2)
    MAX_PREVIEW_PAGES = _int("MAX_PREVIEW_PAGES", 30)
    PREVIEW_WIDTH_PX = _int("PREVIEW_WIDTH_PX", 900)


class TestingConfig(Config):
    ENVIRONMENT = "testing"
    TESTING = True
    SECRET_KEY = "testing-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
