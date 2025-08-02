"""Discord bot commands for UB Stock Bot."""

import discord
import logging
from typing import TYPE_CHECKING

from config import DEFAULT_SELECTOR

if TYPE_CHECKING:
    from product_manager import ProductManager


class BotCommands:
    """Contains all Discord bot slash commands."""

    def __init__(self, product_manager: "ProductManager"):
        self.product_manager = product_manager

    @discord.app_commands.command(
        name="ping", description="Test if the bot is responding"
    )
    async def ping(self, interaction: discord.Interaction):
        """Simple ping command to test bot responsiveness."""
        await interaction.response.send_message(
            "🏓 Pong! Bot is working!", ephemeral=True
        )
        logging.info(f"Ping command used by {interaction.user}")

    @discord.app_commands.command(
        name="add_product", description="Add a new product to monitor"
    )
    async def add_product(
        self,
        interaction: discord.Interaction,
        name: str,
        url: str,
        selector: str = DEFAULT_SELECTOR,
    ):
        """Add a new product to the monitoring list."""
        if self.product_manager.add_product(name, url, selector):
            await interaction.response.send_message(
                f"✅ Added product '{name}' to monitoring list!"
            )
            logging.info(f"Added product '{name}' by {interaction.user}")
        else:
            await interaction.response.send_message(
                f"Product '{name}' already exists!", ephemeral=True
            )

    @discord.app_commands.command(
        name="remove_product", description="Remove a product from monitoring"
    )
    async def remove_product(self, interaction: discord.Interaction, name: str):
        """Remove a product from the monitoring list."""
        if self.product_manager.remove_product(name):
            await interaction.response.send_message(
                f"🗑️ Removed product '{name}' from monitoring list!"
            )
            logging.info(f"Removed product '{name}' by {interaction.user}")
        else:
            await interaction.response.send_message(
                f"Product '{name}' not found!", ephemeral=True
            )

    @discord.app_commands.command(
        name="list_products", description="List all monitored products"
    )
    async def list_products(self, interaction: discord.Interaction):
        """List all currently monitored products."""
        message = self.product_manager.get_product_list_display()
        await interaction.response.send_message(message)


# Create standalone command functions for the command tree
def create_commands(product_manager: "ProductManager"):
    """Create command instances that can be added to the command tree."""
    bot_commands = BotCommands(product_manager)

    @discord.app_commands.command(
        name="ping", description="Test if the bot is responding"
    )
    async def ping(interaction: discord.Interaction):
        await bot_commands.ping(interaction)

    @discord.app_commands.command(
        name="add_product", description="Add a new product to monitor"
    )
    async def add_product(
        interaction: discord.Interaction,
        name: str,
        url: str,
        selector: str = DEFAULT_SELECTOR,
    ):
        await bot_commands.add_product(interaction, name, url, selector)

    @discord.app_commands.command(
        name="remove_product", description="Remove a product from monitoring"
    )
    async def remove_product(interaction: discord.Interaction, name: str):
        await bot_commands.remove_product(interaction, name)

    @discord.app_commands.command(
        name="list_products", description="List all monitored products"
    )
    async def list_products(interaction: discord.Interaction):
        await bot_commands.list_products(interaction)

    return ping, add_product, remove_product, list_products
