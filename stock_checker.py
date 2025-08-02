"""Stock checking module for UB Stock Bot."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Tuple

from bs4 import BeautifulSoup

from product_manager import Product
from utils import fetch_product_page

if TYPE_CHECKING:
    import discord


class StockChecker:
    """Handles stock checking and notifications."""

    @staticmethod
    def parse_stock_status(content: str, selector: str) -> bool:
        """Parse HTML content to check for stock availability."""
        soup = BeautifulSoup(content, "html.parser")
        return bool(soup.select_one(selector))

    @staticmethod
    async def check_single_product(product: Product) -> Tuple[bool, Optional[str]]:
        """
        Check stock for a single product.
        Returns (in_stock, error_message).
        """
        try:
            content = await fetch_product_page(product.url)
            in_stock = StockChecker.parse_stock_status(content, product.selector)
            return in_stock, None
        except Exception as e:
            error_msg = f"Error checking stock for {product.name}: {e}"
            logging.error(error_msg)
            return False, error_msg

    @staticmethod
    async def send_stock_notification(
        client: "discord.Client",
        channel_id: int,
        product: Product,
        was_in_stock: bool,
        is_in_stock: bool,
    ) -> None:
        """Send stock notification to Discord channel."""
        try:
            channel = client.get_channel(channel_id)
            if not channel:
                logging.error(f"Could not find Discord channel with ID: {channel_id}")
                return

            # Only send notification if stock status changed from out-of-stock
            # to in-stock
            if is_in_stock and not was_in_stock:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = f"""🚨 **{product.name} is back in stock!** 🚨
**Link:** {product.url}
**Detected:** {current_time}"""
                await channel.send(message)
                logging.info(f"Sent stock notification for {product.name}")
            elif is_in_stock and was_in_stock:
                logging.info(f"{product.name} still in stock: {product.url}")
            elif not is_in_stock:
                if was_in_stock:
                    logging.warning(f"{product.name} is out of stock: {product.url}")
                else:
                    logging.info(f"{product.name} still out of stock: {product.url}")

        except Exception as e:
            logging.error(f"Error sending notification for {product.name}: {e}")

    @staticmethod
    async def check_all_products(
        client: "discord.Client", channel_id: int, products: dict[str, Product]
    ) -> None:
        """Check stock for all products and send notifications."""
        logging.debug("Checking stock for all products")

        for product in products.values():
            was_in_stock = product.in_stock
            is_in_stock, error = await StockChecker.check_single_product(product)

            if error is None:  # Only update and notify if check was successful
                product.in_stock = is_in_stock
                await StockChecker.send_stock_notification(
                    client, channel_id, product, was_in_stock, is_in_stock
                )
