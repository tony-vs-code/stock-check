"""
UB Stock Bot - Discord bot for monitoring product stock availability.

A Discord bot that monitors product pages for stock availability and sends
notifications when products come back in stock.
"""

import asyncio
import logging
import sys

import discord

from config import DISCORD_CHANNEL_ID, DISCORD_TOKEN
from discord_client import UBStockClient
from log_manager import LogManager
from product_manager import ProductManager


async def main():
    """Main function to set up and run the Discord bot."""
    # Setup logging
    LogManager.setup_logging()

    # Load products
    product_manager = ProductManager()

    # Validate environment variables
    if not DISCORD_TOKEN:
        logging.critical("DISCORD_TOKEN is not set in the .env file. Exiting.")
        sys.exit(1)
    if not DISCORD_CHANNEL_ID:
        logging.critical(
            "DISCORD_CHANNEL_ID is not set or invalid in the .env file. Exiting."
        )
        sys.exit(1)

    # Setup Discord client
    intents = discord.Intents.default()
    client = UBStockClient(
        intents=intents, channel_id=DISCORD_CHANNEL_ID, product_manager=product_manager
    )

    # Start the Discord bot
    try:
        await client.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        logging.critical("Failed to log in to Discord. Invalid DISCORD_TOKEN.")
        sys.exit(1)
    except discord.HTTPException as e:
        logging.critical(f"HTTP Exception occurred during Discord client startup: {e}")
        sys.exit(1)
    except Exception as e:
        logging.critical(
            f"An unexpected error occurred during Discord client startup: {e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(f"Unhandled exception in main execution: {e}")
        sys.exit(1)
