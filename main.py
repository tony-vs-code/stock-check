import discord
from discord.ext import tasks
from decouple import config
from bs4 import BeautifulSoup

import os
import requests
import logging
from datetime import datetime

# List of products to check if they are in stock.
TARGET_URLS = {
    "G5 PTZ": {
        "url": "https://store.ui.com/us/en/category/all-cameras-nvrs/products/uvc-g5-ptz",
        "selector": "button[label='Add to Cart']",
    },
    "G5 Turret Ultra": {
        "url": "https://store.ui.com/us/en/category/cameras-dome-turret/products/uvc-g5-turret-ultra?variant=uvc-g5-turret-ultra",
        "selector": "button[label='Add to Cart']",
    },
}
# Example of other methods to check items.
# "Product 2": {"url": "https://example.com/product2", "selector": ".add-to-cart-button"},
# "Product 3": {"url": "https://example.com/product3", "selector": "#add-to-cart"},


# Send message to discord channel
async def send_message(message: str) -> None:
    try:
        channel = client.get_channel(int(DISCORD_CHANNEL_ID))
        if channel:
            await channel.send(message)
        else:
            logging.error(f"Channel with ID {DISCORD_CHANNEL_ID} not found.")
    except Exception as e:
        logging.error(f"Error sending message to discord channel: {e}")


# Check if the product is in stock
@tasks.loop(seconds=15)
async def check_stock():
    try:
        for product, info in TARGET_URLS.items():
            url = info["url"]
            selector = info["selector"]
            response = requests.get(url)
            soup = BeautifulSoup(response.content, "html.parser")
            add_to_cart_button = soup.select_one(selector)
            if add_to_cart_button:
                await send_message(f"{product} is in stock: {url}")
                logging.info(f"{product} is in stock. Sent message to discord channel.")
            else:
                logging.info(f"{product} is not in stock. It was last checked at {datetime.now()}")
    except Exception as e:
        logging.error(f"Error checking stock: {e}")


# Every week on Monday, lets clean up the log file.
@tasks.loop(hours=168)
async def clean_log():
    try:
        current_log_file = "log/ui_stock_bot.log"
        for file in os.listdir("log"):
            file_path = os.path.join("log", file)
            if file.endswith(".log") and file_path != current_log_file:
                # Check if the file was modified more than a week ago
                if os.path.getmtime(file_path) < (datetime.now().timestamp() - 7 * 24 * 60 * 60):
                    os.remove(file_path)
                    logging.info(f"Deleted old log file: {file_path}")
    except Exception as e:
        logging.error(f"Error cleaning log: {e}")


class MyClient(discord.Client):
    async def on_ready(self):
        logging.info(f"Logged in as {self.user}")
        check_stock.start()
        clean_log.start()


if __name__ == "__main__":
    # Logging
    if not os.path.exists("log"):
        os.makedirs("log")

    logging.basicConfig(
        filename="log/ui_stock_bot.log",
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # reset the log file on startup
    open("log/ui_stock_bot.log", "w").close()

    # Discord bot token and channel ID
    DISCORD_TOKEN = config("DISCORD_TOKEN")
    DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID")

    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        logging.error("DISCORD_TOKEN or DISCORD_CHANNEL_ID is not set in the .env file.")
        exit(1)

    # Discord bot client
    client = MyClient(intents=discord.Intents.default())

    client.run(DISCORD_TOKEN)
