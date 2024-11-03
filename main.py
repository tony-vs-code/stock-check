import discord
import os
import logging
import asyncio
import aiohttp
from discord.ext import tasks
from decouple import config
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Any

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


def retry(retries: int = 3, delay: int = 5):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except aiohttp.ClientError as e:
                    logging.error(f"Attempt {attempt + 1} failed: {e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
            raise Exception(f"All {retries} attempts failed.")

        return wrapper

    return decorator


# Send message to discord channel
async def send_message(message: str) -> None:
    try:
        channel = client.get_channel(int(DISCORD_CHANNEL_ID))
        if channel:
            await channel.send(message)
        else:
            logging.error(f"Channel with ID {DISCORD_CHANNEL_ID} not found.")
    except discord.DiscordException as e:
        logging.error(f"Error sending message to discord channel: {e}")


@retry()
async def fetch_product_page(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise aiohttp.ClientError(f"Failed to fetch {url}: Status code {response.status}")
            return await response.text()


def parse_stock_status(content: str, selector: str) -> bool:
    soup = BeautifulSoup(content, "html.parser")
    return bool(soup.select_one(selector))


async def check_product_stock(product: str, info: Dict[str, Any]) -> None:
    try:
        content = await fetch_product_page(info["url"])
        in_stock = parse_stock_status(content, info["Selector"])
        if in_stock and not stock_status[product]:
            stock_status[product] = True
            await send_message(f"{product} is in stock: {info['url']}")
        elif not in_stock and stock_status[product]:
            stock_status[product] = False
            logging.warning(f"{product} is out of stock: {info['url']}")
    except Exception as e:
        logging.error(f"Error checking stock for {product}: {e}")


@tasks.loop(seconds=15)
async def check_stock():
    logging.debug("check_stock task is running")
    for product, info in TARGET_URLS.items():
        await check_product_stock(product, info)


# Every week on Monday, lets clean up the logs file.
@tasks.loop(hours=168)
async def clean_logs():
    logging.debug("clean_logs task is running")
    try:
        current_log_file = "logs/ui_stock_bot.log"
        for file in os.listdir("logs"):
            file_path = os.path.join("logs", file)
            if file.endswith(".log") and file_path != current_log_file:
                # Check if the file was modified more than a week ago
                if os.path.getmtime(file_path) < (datetime.now().timestamp() - 7 * 24 * 60 * 60):
                    os.remove(file_path)
                    logging.info(f"Deleted old log file: {file_path}")
    except Exception as e:
        logging.error(f"Error cleaning logs: {e}")


class MyClient(discord.Client):
    async def on_ready(self):
        logging.info(f"Logged in as {self.user}")
        if not check_stock.is_running():
            check_stock.start()
        if not clean_logs.is_running():
            clean_logs.start()


if __name__ == "__main__":
    # Logging
    if not os.path.exists("logs"):
        os.makedirs("logs")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("logs/ui_stock_bot.log"),
            logging.StreamHandler(),  # This will log to the console
        ],
    )

    # reset the log file on startup
    open("logs/ui_stock_bot.log", "w").close()

    # Discord bot token and channel ID
    DISCORD_TOKEN = config("DISCORD_TOKEN")
    DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID")

    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        logging.error("DISCORD_TOKEN or DISCORD_CHANNEL_ID is not set in the .env file.")
        exit(1)

    # Discord bot client
    intents = discord.Intents.default()
    client = MyClient(intents=intents)

    client.run(DISCORD_TOKEN)
