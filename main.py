import discord
import os
import logging
import asyncio
from discord.ext import tasks
from decouple import config
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Any
from utils import fetch_product_page
import logging.handlers  # Import for RotatingFileHandler
from pathlib import Path  # For cleaner path manipulation in clean_logs

# --- Environment Variable Loading ---
# Load environment variables, providing default values and
# casting to appropriate types
DISCORD_TOKEN = config("DISCORD_TOKEN", default="")  # Provide a default empty string
DISCORD_CHANNEL_ID = config(
    "DISCORD_CHANNEL_ID", default=0, cast=int
)  # Default 0 will trigger check
CHECK_STOCK_INTERVAL = config("CHECK_STOCK_INTERVAL", default=15, cast=int)  # seconds
CLEAN_LOGS_INTERVAL = config(
    "CLEAN_LOGS_INTERVAL", default=168, cast=int
)  # hours (7 days)

# List of products to check if they are in stock.
TARGET_URLS = {
    "G5 PTZ": {
        "url": "https://store.ui.com/us/en/category/all-cameras-nvrs/products/uvc-g5-ptz",
        "Selector": "button[label='Add to Cart']",
    },
    "G5 Turret Ultra": {
        "url": "https://store.ui.com/us/en/category/cameras-dome-turret/products/uvc-g5-turret-ultra?variant=uvc-g5-turret-ultra",
        "Selector": "button[label='Add to Cart']",
    },
}

# Dictionary to keep track of stock status
stock_status = {product: False for product in TARGET_URLS}


# Send message to discord channel
async def send_message(client: discord.Client, channel_id: int, message: str) -> None:
    """Sends a message to the specified Discord channel."""
    try:
        channel = client.get_channel(channel_id)
        if channel:
            logging.info(
                f"Attempting to send message to channel {channel_id}: {message}"
            )
            await channel.send(message)
            logging.info(f"Message sent successfully to channel {channel_id}.")
        else:
            logging.error(
                f"Could not find Discord channel with ID: {channel_id}. "
                "Please check DISCORD_CHANNEL_ID."
            )
    except discord.errors.Forbidden:
        logging.error(
            f"Bot does not have permissions to send messages in channel {channel_id}."
        )
    except discord.errors.HTTPException as e:
        logging.error(
            f"HTTP exception occurred while sending message to channel "
            f"{channel_id}: {e}"
        )
    except Exception as e:
        logging.error(
            f"An unexpected error occurred while sending message to channel "
            f"{channel_id}: {e}"
        )


def parse_stock_status(content: str, selector: str) -> bool:
    """Parses HTML content to check for the presence of a specific selector."""
    soup = BeautifulSoup(content, "html.parser")
    return bool(soup.select_one(selector))


async def check_product_stock(
    client: discord.Client, channel_id: int, product: str, info: Dict[str, Any]
) -> None:
    """Checks the stock status of a single product and sends Discord notifications."""
    try:
        content = await fetch_product_page(info["url"])
        in_stock = parse_stock_status(content, info["Selector"])

        if in_stock and stock_status[product]:
            logging.info(f"{product} No message sent, still in stock: {info['url']}")
        elif in_stock and not stock_status[product]:
            stock_status[product] = True
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"""🚨 **{product} is back in stock!** 🚨
**Link:** {info["url"]}
**Detected:** {current_time}"""
            await send_message(client, channel_id, message)
        elif not in_stock:
            if stock_status[product]:
                stock_status[product] = False
                logging.warning(f"{product} is out of stock: {info['url']}")
            else:
                logging.info(f"{product} is still out of stock: {info['url']}")
    except Exception as e:
        logging.error(f"Error checking stock for {product}: {e}")


@tasks.loop(seconds=CHECK_STOCK_INTERVAL)
async def check_stock(client: discord.Client, channel_id: int):
    """Periodically checks the stock for all defined products."""
    logging.debug("check_stock task is running")
    for product, info in TARGET_URLS.items():
        await check_product_stock(client, channel_id, product, info)


@tasks.loop(hours=CLEAN_LOGS_INTERVAL)
async def clean_logs():
    """Periodically cleans up old log files from the 'logs' directory."""
    logging.debug("clean_logs task is running")
    try:
        log_dir = Path("logs")
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)  # Ensure dir exists

        current_log_file_name = "ui_stock_bot.log"  # Name of the main log file

        for file_path in log_dir.iterdir():
            # Check if it's a file, ends with .log, and is not the current main log file
            if (
                file_path.is_file()
                and file_path.suffix == ".log"
                and file_path.name != current_log_file_name
            ):
                # Check if the file was modified more than a week ago (7 days)
                seven_days_ago = datetime.now().timestamp() - (7 * 24 * 60 * 60)
                if file_path.stat().st_mtime < seven_days_ago:
                    os.remove(file_path)
                    logging.info(f"Deleted old log file: {file_path}")
    except Exception as e:
        logging.error(f"Error cleaning logs: {e}")


class MyClient(discord.Client):
    """Custom Discord client class."""

    def __init__(self, intents: discord.Intents, *args, **kwargs):
        super().__init__(intents=intents, *args, **kwargs)
        self.channel_id = None  # Will be set in main() before client.start()

    async def on_ready(self):
        """Called when the bot successfully connects to Discord."""
        logging.info(f"Logged in as {self.user}")

        if not self.channel_id:  # Check if channel_id is not set (0 or None)
            logging.critical(
                "Discord channel ID is not set or invalid. Bot cannot send messages."
            )
            return

        if not check_stock.is_running():
            check_stock.start(self, self.channel_id)
            logging.info(
                f"check_stock task started with interval {CHECK_STOCK_INTERVAL}s"
            )
        else:
            logging.info("check_stock task is already running.")

        if not clean_logs.is_running():
            clean_logs.start()
            logging.info(
                f"clean_logs task started with interval {CLEAN_LOGS_INTERVAL}h"
            )
        else:
            logging.info("clean_logs task is already running.")


async def main():
    """Main function to set up logging, load config, and run the Discord bot."""
    # 1. Logging Setup
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file_path = os.path.join(log_dir, "ui_stock_bot.log")

    # Configure logging with RotatingFileHandler
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # File handler for main log file, rotates after 5MB, keeps 5 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_file_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,  # Keep 5 backup files
        encoding="utf-8",  # Specify encoding
    )
    file_handler.setFormatter(log_formatter)

    # Stream handler for console output
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_formatter)

    # Get root logger and add handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(
        logging.INFO
    )  # Set overall logging level (INFO for production)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    # 2. Check for required environment variables
    if not DISCORD_TOKEN:
        root_logger.critical("DISCORD_TOKEN is not set in the .env file. Exiting.")
        exit(1)
    if not DISCORD_CHANNEL_ID:  # This will be 0 if default is used and no env var set
        root_logger.critical(
            "DISCORD_CHANNEL_ID is not set or invalid in the .env file. Exiting."
        )
        exit(1)

    # 3. Discord bot client setup
    intents = discord.Intents.default()

    client = MyClient(intents=intents)
    client.channel_id = DISCORD_CHANNEL_ID  # Pass the channel ID to the client instance

    # 4. Start the Discord bot
    try:
        await client.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        root_logger.critical("Failed to log in to Discord. Invalid DISCORD_TOKEN.")
        exit(1)
    except discord.HTTPException as e:
        root_logger.critical(
            f"HTTP Exception occurred during Discord client startup: {e}"
        )
        exit(1)
    except Exception as e:
        root_logger.critical(
            f"An unexpected error occurred during Discord client startup: {e}"
        )
        exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Graceful shutdown on Ctrl+C
        logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(f"Unhandled exception in main execution: {e}")
        exit(1)
