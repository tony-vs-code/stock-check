"""Configuration module for UB Stock Bot."""

import os


def _get_env_int(name: str, default: int) -> int:
    """Safely parse an integer environment variable, falling back to default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Application metadata
APP_VERSION = "0.4.0"

# Optional environment variables (used to seed DB settings on first run)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_CHANNEL_ID = _get_env_int("DISCORD_CHANNEL_ID", 0)
CHECK_STOCK_INTERVAL = _get_env_int("CHECK_STOCK_INTERVAL", 15)  # seconds
CLEAN_LOGS_INTERVAL = _get_env_int("CLEAN_LOGS_INTERVAL", 168)  # hours

# Server configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = _get_env_int("PORT", 8000)

# Paths
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "ubstock.db"))
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "ui_stock_bot.log")
STATIC_DIR = os.getenv("STATIC_DIR", "static")

# Default selector for stock checking
DEFAULT_SELECTOR = "button[label='Add to Cart']"
