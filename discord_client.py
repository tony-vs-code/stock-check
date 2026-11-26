"""Discord client and lifecycle manager for UB Stock Bot."""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import tasks

from config import CLEAN_LOGS_INTERVAL
from database import Database
from log_manager import LogManager
from product_manager import ProductManager

logger = logging.getLogger(__name__)


class UBStockClient(discord.Client):
    """Custom Discord client for UB Stock Bot slash commands and status."""

    def __init__(
        self,
        intents: discord.Intents,
        channel_id: int,
        product_manager: ProductManager,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(intents=intents, *args, **kwargs)
        self.channel_id = channel_id
        self.product_manager = product_manager
        self.tree = discord.app_commands.CommandTree(self)
        self.startup_message_sent = False

    async def setup_hook(self) -> None:
        """Register and sync Discord slash commands."""
        logger.info("Setting up slash commands...")

        from bot_commands import create_commands

        ping, add_product, remove_product, list_products = create_commands(
            self.product_manager
        )

        self.tree.add_command(ping)
        self.tree.add_command(add_product)
        self.tree.add_command(remove_product)
        self.tree.add_command(list_products)

        try:
            synced = await self.tree.sync()
            logger.info("Successfully synced %d command(s)", len(synced))
        except Exception as exc:
            logger.error("Failed to sync commands: %s", exc)

    async def send_startup_message_if_needed(self) -> None:
        """Send a helpful message if no products are configured."""
        if self.product_manager.is_empty() and not self.startup_message_sent:
            try:
                channel = self.get_channel(self.channel_id)
                if channel:
                    startup_message = (
                        "👋 **UB Stock Bot is ready!**\n\n"
                        "🚨 **No products are currently being monitored.**\n\n"
                        "**Available Commands:**\n"
                        "• `/add_product` - Add a new product to monitor\n"
                        "• `/list_products` - View all monitored products\n"
                        "• `/remove_product` - Remove a product from monitoring\n\n"
                        "You can also manage everything from the Web UI!"
                    )
                    await channel.send(startup_message)
                    logger.info("Sent startup message with command instructions")
                    self.startup_message_sent = True
                else:
                    logger.warning(
                        "Could not find Discord channel with ID: %s", self.channel_id
                    )
            except Exception as exc:
                logger.error("Error sending startup message: %s", exc)

    async def on_ready(self) -> None:
        """Called when the bot successfully connects to Discord."""
        if self.user:
            logger.info("Logged in to Discord as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guild(s)", len(self.guilds))

        if not self.channel_id:
            logger.warning(
                "Discord channel ID is not set or invalid. Bot cannot send messages."
            )
            return

        try:
            channel = self.get_channel(self.channel_id)
            if channel:
                logger.info(
                    "Found Discord channel: %s (ID: %s)", channel.name, channel.id
                )
            else:
                logger.warning("Could not find channel with ID: %s", self.channel_id)
                return
        except Exception as exc:
            logger.error("Error accessing channel %s: %s", self.channel_id, exc)
            return

        await self.send_startup_message_if_needed()

        if not self.clean_logs.is_running():
            self.clean_logs.start()

    @tasks.loop(hours=CLEAN_LOGS_INTERVAL)
    async def clean_logs(self) -> None:
        """Periodically clean up old log files."""
        LogManager.clean_old_logs()


class DiscordManager:
    """Manages optional Discord client lifecycle without crashing when unconfigured."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.client: Optional[UBStockClient] = None
        self._task: Optional[asyncio.Task] = None
        self._active_token: str = ""

    def get_credentials(self) -> tuple[str, str]:
        """Return (token, channel_id) from database settings."""
        token = (self.db.get_setting("discord_token", "") or "").strip()
        channel_id = (self.db.get_setting("discord_channel_id", "") or "").strip()
        return token, channel_id

    def is_configured(self) -> bool:
        """Return True if both Discord token and channel ID are set in DB."""
        token, channel_id = self.get_credentials()
        return bool(token and channel_id)

    def is_connected(self) -> bool:
        """Return True if the Discord Gateway client is connected and ready."""
        return (
            self.client is not None
            and not self.client.is_closed()
            and self.client.is_ready()
        )

    async def start(self) -> None:
        """Start Discord client if configured, else log a friendly notice."""
        token, raw_channel_id = self.get_credentials()
        if not token or not raw_channel_id:
            logger.info(
                "Discord notifications unconfigured. You can configure them "
                "anytime in the Web UI or via API."
            )
            return

        try:
            channel_id = int(raw_channel_id)
        except ValueError:
            logger.warning(
                "Invalid discord_channel_id '%s'. Discord bot will not connect.",
                raw_channel_id,
            )
            return

        if self._task and not self._task.done():
            return

        intents = discord.Intents.default()
        product_manager = ProductManager(db=self.db)
        self.client = UBStockClient(
            intents=intents,
            channel_id=channel_id,
            product_manager=product_manager,
        )
        self._active_token = token
        self._task = asyncio.create_task(self._run_client(token))

    async def _run_client(self, token: str) -> None:
        """Run the Discord client while catching errors so the web server stays up."""
        assert self.client is not None
        try:
            await self.client.start(token)
        except asyncio.CancelledError:
            logger.info("Discord client task cancelled.")
        except discord.LoginFailure:
            logger.error(
                "Failed to log in to Discord: invalid bot token. "
                "Update credentials in the Web UI."
            )
        except discord.HTTPException as exc:
            logger.error("Discord HTTP error during client execution: %s", exc)
        except Exception as exc:
            logger.error("Unexpected error in Discord client: %s", exc)
        finally:
            if self.client and not self.client.is_closed():
                await self.client.close()

    async def stop(self) -> None:
        """Gracefully close the Discord client and cancel its background task."""
        if self.client and not self.client.is_closed():
            try:
                await self.client.close()
            except Exception as exc:
                logger.warning("Error closing Discord client: %s", exc)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.client = None
        self._active_token = ""

    async def restart(self) -> None:
        """Stop any running Discord client and start again with updated DB settings."""
        await self.stop()
        await self.start()
