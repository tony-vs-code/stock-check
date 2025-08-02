"""Configuration module for UB Stock Bot."""

import os
from decouple import config

# Environment Variables
DISCORD_TOKEN = config("DISCORD_TOKEN", default="")
DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID", default=0, cast=int)
CHECK_STOCK_INTERVAL = config("CHECK_STOCK_INTERVAL", default=15, cast=int)  # seconds
CLEAN_LOGS_INTERVAL = config("CLEAN_LOGS_INTERVAL", default=168, cast=int)  # hours

# Paths
DATA_DIR = os.getenv("DATA_DIR", "data")
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "ui_stock_bot.log")

# Default selector for stock checking
DEFAULT_SELECTOR = "button[label='Add to Cart']"
