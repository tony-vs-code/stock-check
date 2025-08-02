"""Discord client for UB Stock Bot."""

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from config import CHECK_STOCK_INTERVAL, CLEAN_LOGS_INTERVAL
from log_manager import LogManager
from stock_checker import StockChecker

if TYPE_CHECKING:
    from product_manager import ProductManager


class UBStockClient(discord.Client):
    """Custom Discord client for UB Stock Bot."""

    def __init__(
        self,
        intents: discord.Intents,
        channel_id: int,
        product_manager: "ProductManager",
        *args,
        **kwargs
    ):
        super().__init__(intents=intents, *args, **kwargs)
        self.channel_id = channel_id
        self.product_manager = product_manager
        self.tree = discord.app_commands.CommandTree(self)
        self.startup_message_sent = False

    async def setup_hook(self):
        """Called when the client is ready to sync commands."""
        logging.info("Setting up slash commands...")
        
        # Import and add commands to the tree
        from bot_commands import create_commands
        ping, add_product, remove_product, list_products = create_commands(
            self.product_manager
        )
        
        self.tree.add_command(ping)
        self.tree.add_command(add_product)
        self.tree.add_command(remove_product)
        self.tree.add_command(list_products)
        
        # List all commands before syncing
        logging.info(
            "Commands to sync: %s",
            [cmd.name for cmd in self.tree.get_commands()]
        )
        
        try:
            synced = await self.tree.sync()
            logging.info(f"Successfully synced {len(synced)} command(s)")
            for cmd in synced:
                logging.info(f"  ✓ {cmd.name}: {cmd.description}")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")
            import traceback
            logging.error(traceback.format_exc())

    async def send_startup_message_if_needed(self):
        """Send a helpful message if no products are configured."""
        if self.product_manager.is_empty() and not self.startup_message_sent:
            try:
                channel = self.get_channel(self.channel_id)
                if channel:
                    startup_message = """👋 **UB Stock Bot is ready!**

🚨 **No products are currently being monitored.**

**Available Commands:**
• `/add_product` - Add a new product to monitor
• `/list_products` - View all monitored products  
• `/remove_product` - Remove a product from monitoring

**Examples:**
`/add_product name:G5 PTZ url:https://store.ui.com/us/en/products/uvc-g5-ptz`
`/add_product name:Custom Item url:https://example.com/product selector:.buy-now-button`

Get started by adding your first product! 🚀"""
                    
                    await channel.send(startup_message)
                    logging.info("Sent startup message with command instructions")
                    self.startup_message_sent = True
                else:
                    logging.error(
                        f"Could not find Discord channel with ID: {self.channel_id}"
                    )
            except Exception as e:
                logging.error(f"Error sending startup message: {e}")

    async def on_ready(self):
        """Called when the bot successfully connects to Discord."""
        logging.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logging.info(f"Connected to {len(self.guilds)} guild(s)")
        
        # Log guild information
        for guild in self.guilds:
            logging.info(f"  - Guild: {guild.name} (ID: {guild.id})")
            # Check if bot has relevant permissions
            me = guild.me
            if me:
                perms = me.guild_permissions
                logging.info(
                    f"    Bot permissions in {guild.name}: "
                    f"send_messages={perms.send_messages}, "
                    f"view_channel={perms.view_channel}, "
                    f"embed_links={perms.embed_links}"
                )

        if not self.channel_id:
            logging.critical(
                "Discord channel ID is not set or invalid. Bot cannot send messages."
            )
            return

        # Verify channel access
        try:
            channel = self.get_channel(self.channel_id)
            if channel:
                logging.info(
                    f"Found channel: {channel.name} (ID: {channel.id}) "
                    f"in guild: {channel.guild.name}"
                )
                
                # Check channel-specific permissions
                perms = channel.permissions_for(channel.guild.me)
                logging.info(
                    f"Channel permissions: send_messages={perms.send_messages}, "
                    f"view_channel={perms.view_channel}"
                )
            else:
                logging.error(f"Could not find channel with ID: {self.channel_id}")
                return
        except Exception as e:
            logging.error(f"Error accessing channel {self.channel_id}: {e}")
            return

        # Send startup message if no products are configured
        await self.send_startup_message_if_needed()

        # Start background tasks
        if not self.check_stock.is_running():
            self.check_stock.start()
            logging.info(
                f"check_stock task started with interval {CHECK_STOCK_INTERVAL}s"
            )

        if not self.clean_logs.is_running():
            self.clean_logs.start()
            logging.info(
                f"clean_logs task started with interval {CLEAN_LOGS_INTERVAL}h"
            )

    @tasks.loop(seconds=CHECK_STOCK_INTERVAL)
    async def check_stock(self):
        """Periodically check stock for all products."""
        await StockChecker.check_all_products(
            self, self.channel_id, self.product_manager.get_all_products()
        )

    @tasks.loop(hours=CLEAN_LOGS_INTERVAL)
    async def clean_logs(self):
        """Periodically clean up old log files."""
        LogManager.clean_old_logs()
