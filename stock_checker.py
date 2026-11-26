"""Stock checking, Discord REST notifications, and background monitor loop."""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Tuple, Union

import aiohttp
from bs4 import BeautifulSoup

from database import Database, utc_now_iso
from product_manager import Product
from utils import fetch_product_page

if TYPE_CHECKING:
    import discord

    from discord_client import DiscordManager

logger = logging.getLogger(__name__)


async def send_discord_rest_message(
    token: str, channel_id: str, message: str
) -> Tuple[bool, Optional[str]]:
    """Send a message to a Discord channel using the Discord REST API."""
    clean_token = token.strip()
    clean_channel = str(channel_id).strip()
    if not clean_token or not clean_channel:
        return False, "Discord token and channel ID are required."

    url = f"https://discord.com/api/v10/channels/{clean_channel}/messages"
    headers = {
        "Authorization": f"Bot {clean_token}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, headers=headers, json={"content": message}
            ) as response:
                if response.status in (200, 201, 204):
                    return True, None
                body = await response.text()
                return (
                    False,
                    f"Discord API error (HTTP {response.status}): {body[:200]}",
                )
    except Exception as exc:
        return False, f"Failed to reach Discord API: {exc}"


class StockChecker:
    """Handles HTML stock parsing, single-product checks, and selector tests."""

    @staticmethod
    def parse_stock_status(content: str, selector: str) -> bool:
        """Parse HTML content to check for stock availability."""
        soup = BeautifulSoup(content, "html.parser")
        return bool(soup.select_one(selector))

    @staticmethod
    def validate_selector(selector: str) -> Optional[str]:
        """Validate a CSS selector string. Returns an error message or None."""
        if not selector or not selector.strip():
            return "Selector cannot be empty."
        try:
            BeautifulSoup("", "html.parser").select_one(selector)
            return None
        except Exception as exc:
            return f"Invalid CSS selector '{selector}': {exc}"

    @staticmethod
    async def check_single_product(
        product: Union[Product, dict[str, Any]],
    ) -> Tuple[bool, Optional[str]]:
        """Check stock for a single product. Returns (in_stock, error_message)."""
        name = product["name"] if isinstance(product, dict) else product.name
        url = product["url"] if isinstance(product, dict) else product.url
        selector = (
            product["selector"] if isinstance(product, dict) else product.selector
        )

        selector_error = StockChecker.validate_selector(selector)
        if selector_error:
            return False, selector_error

        try:
            content = await fetch_product_page(url)
            in_stock = StockChecker.parse_stock_status(content, selector)
            return in_stock, None
        except Exception as exc:
            error_msg = f"Error checking stock for {name}: {exc}"
            logger.error(error_msg)
            return False, error_msg

    @staticmethod
    async def test_selector(url: str, selector: str) -> dict[str, Any]:
        """Test a URL and CSS selector without saving to the database."""
        selector_error = StockChecker.validate_selector(selector)
        if selector_error:
            return {"in_stock": False, "found_selector": False, "error": selector_error}

        try:
            content = await fetch_product_page(url)
            found = StockChecker.parse_stock_status(content, selector)
            return {"in_stock": found, "found_selector": found, "error": None}
        except Exception as exc:
            return {
                "in_stock": False,
                "found_selector": False,
                "error": str(exc),
            }

    @staticmethod
    async def send_stock_notification(
        client: "discord.Client",
        channel_id: int,
        product: Product,
        was_in_stock: bool,
        is_in_stock: bool,
    ) -> None:
        """Send stock notification via an active Discord client."""
        try:
            channel = client.get_channel(channel_id)
            if not channel:
                logger.error("Could not find Discord channel with ID: %s", channel_id)
                return

            if is_in_stock and not was_in_stock:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = (
                    f"🚨 **{product.name} is back in stock!** 🚨\n"
                    f"**Link:** {product.url}\n"
                    f"**Detected:** {current_time}"
                )
                await channel.send(message)
                logger.info("Sent stock notification for %s", product.name)
        except Exception as exc:
            logger.error("Error sending notification for %s: %s", product.name, exc)


class StockMonitor:
    """Periodic stock monitoring service backed by SQLite and optional Discord."""

    def __init__(
        self,
        db: Database,
        discord_manager: Optional["DiscordManager"] = None,
    ) -> None:
        self.db = db
        self.discord_manager = discord_manager
        self.is_checking: bool = False
        self.last_check: Optional[str] = None
        self._check_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._wakeup_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def get_check_interval(self) -> int:
        """Read the check interval (seconds) from DB settings."""
        raw = self.db.get_setting("check_stock_interval", "15") or "15"
        try:
            value = int(raw)
            return max(1, value)
        except ValueError:
            return 15

    def notifications_enabled(self) -> bool:
        """Return whether stock notifications are enabled in DB settings."""
        raw = (self.db.get_setting("notifications_enabled", "true") or "true").lower()
        return raw in ("true", "1", "yes", "on")

    async def _send_in_stock_alert(
        self, product: dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """Send an in-stock Discord notification if enabled and configured."""
        if not self.notifications_enabled():
            return False, None

        token = (self.db.get_setting("discord_token", "") or "").strip()
        channel_id = (self.db.get_setting("discord_channel_id", "") or "").strip()
        if not token or not channel_id:
            return False, None

        detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"🚨 **{product['name']} is back in stock!** 🚨\n"
            f"**Link:** {product['url']}\n"
            f"**Detected:** {detected_at}"
        )

        # Prefer connected Discord gateway client if available and token matches
        if (
            self.discord_manager is not None
            and self.discord_manager.is_connected()
            and self.discord_manager.client is not None
            and self.discord_manager._active_token == token
        ):
            try:
                channel = self.discord_manager.client.get_channel(int(channel_id))
                if channel is not None:
                    await channel.send(message)
                    return True, None
            except Exception as exc:
                logger.warning(
                    "Gateway send failed, falling back to Discord REST API: %s", exc
                )

        return await send_discord_rest_message(token, channel_id, message)

    async def _check_and_record(
        self, product: dict[str, Any], record_unchanged: bool = False
    ) -> dict[str, Any]:
        """Check one product, update SQLite state, notify, and log history."""
        was_in_stock = bool(product["in_stock"])
        is_in_stock, error = await StockChecker.check_single_product(product)
        checked_at = utc_now_iso()

        if error is not None:
            updated = self.db.update_product_check_result(
                product_id=product["id"],
                in_stock=None,
                error=error,
                checked_at=checked_at,
                status_changed=False,
            )
            self.db.add_history(
                product_id=product["id"],
                product_name=product["name"],
                status="error",
                notification_sent=False,
                message=error,
            )
            return updated or product

        status_changed = is_in_stock != was_in_stock
        updated = self.db.update_product_check_result(
            product_id=product["id"],
            in_stock=is_in_stock,
            error=None,
            checked_at=checked_at,
            status_changed=status_changed,
        )

        if is_in_stock and not was_in_stock:
            sent, notify_err = await self._send_in_stock_alert(product)
            msg = f"{product['name']} is back in stock!"
            if notify_err:
                msg = f"{msg} (Notification failed: {notify_err})"
                logger.error(
                    "Notification error for %s: %s", product["name"], notify_err
                )
            self.db.add_history(
                product_id=product["id"],
                product_name=product["name"],
                status="in_stock",
                notification_sent=sent,
                message=msg,
            )
        elif not is_in_stock and was_in_stock:
            self.db.add_history(
                product_id=product["id"],
                product_name=product["name"],
                status="out_of_stock",
                notification_sent=False,
                message=f"{product['name']} went out of stock.",
            )
        elif record_unchanged:
            status_str = "in_stock" if is_in_stock else "out_of_stock"
            state_label = "in stock" if is_in_stock else "out of stock"
            self.db.add_history(
                product_id=product["id"],
                product_name=product["name"],
                status=status_str,
                notification_sent=False,
                message=f"Manual check: {product['name']} is {state_label}.",
            )

        return updated or product

    async def check_product_by_id(self, product_id: int) -> Optional[dict[str, Any]]:
        """Immediately check a single product by ID and log to history."""
        product = self.db.get_product(product_id)
        if product is None:
            return None

        async with self._check_lock:
            self.is_checking = True
            try:
                updated = await self._check_and_record(product, record_unchanged=True)
                self.last_check = utc_now_iso()
                return updated
            finally:
                self.is_checking = False

    async def check_all(self, record_unchanged: bool = False) -> list[dict[str, Any]]:
        """Check all monitored products sequentially under a lock."""
        async with self._check_lock:
            self.is_checking = True
            results: list[dict[str, Any]] = []
            try:
                products = self.db.list_products()
                for product in products:
                    updated = await self._check_and_record(
                        product, record_unchanged=record_unchanged
                    )
                    results.append(updated)
                self.last_check = utc_now_iso()
                return results
            finally:
                self.is_checking = False

    def notify_settings_changed(self) -> None:
        """Wake the background loop so updated intervals take effect immediately."""
        self._wakeup_event.set()

    def start(self) -> None:
        """Start the background stock checking loop."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._wakeup_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background stock checking loop gracefully."""
        self._stop_event.set()
        self._wakeup_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run_loop(self) -> None:
        """Periodic stock checking background loop."""
        logger.info("Background stock monitor started.")
        while not self._stop_event.is_set():
            interval = self.get_check_interval()
            self._wakeup_event.clear()

            stop_task = asyncio.create_task(self._stop_event.wait())
            wake_task = asyncio.create_task(self._wakeup_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {stop_task, wake_task},
                    timeout=interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            except asyncio.CancelledError:
                stop_task.cancel()
                wake_task.cancel()
                break

            if self._stop_event.is_set():
                break

            if wake_task.done() and not stop_task.done():
                # Settings updated; loop around immediately to apply new interval
                continue

            try:
                if self.db.list_products():
                    await self.check_all(record_unchanged=False)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in periodic stock check loop: %s", exc)
