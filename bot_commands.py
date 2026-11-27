"""Discord bot commands for Stock Checker."""

import logging
from typing import TYPE_CHECKING

import discord

from config import DEFAULT_SELECTOR
from stock_checker import VALID_STRATEGIES, StockChecker

if TYPE_CHECKING:
    from product_manager import ProductManager


class BotCommands:
    """Contains all Discord bot slash commands."""

    def __init__(self, product_manager: "ProductManager"):
        self.product_manager = product_manager

    async def ping(self, interaction: discord.Interaction):
        """Simple ping command to test bot responsiveness."""
        await interaction.response.send_message(
            "🏓 Pong! Bot is working!", ephemeral=True
        )
        logging.info(f"Ping command used by {interaction.user}")

    async def add_product(
        self,
        interaction: discord.Interaction,
        name: str,
        url: str,
        strategy: str = "auto",
        selector: str = "",
    ):
        """Add a new product to the monitoring list."""
        cleaned_strategy = (strategy or "auto").strip().lower()
        strategy_err = StockChecker.validate_strategy(cleaned_strategy)
        if strategy_err:
            await interaction.response.send_message(
                f"❌ {strategy_err}", ephemeral=True
            )
            return

        cleaned_selector = (selector or "").strip()
        if cleaned_strategy == "css_selector" and not cleaned_selector:
            cleaned_selector = DEFAULT_SELECTOR

        if cleaned_selector:
            selector_err = StockChecker.validate_selector(cleaned_selector)
            if selector_err:
                await interaction.response.send_message(
                    f"❌ {selector_err}", ephemeral=True
                )
                return

        if self.product_manager.add_product(
            name, url, selector=cleaned_selector, strategy=cleaned_strategy
        ):
            selector_display = (
                cleaned_selector or "(Auto-detect - no selector required)"
            )
            response = (
                "✅ **Successfully added product to monitoring!**\n\n"
                f"**Product:** {name}\n"
                f"**URL:** {url}\n"
                f"**Strategy:** {cleaned_strategy}\n"
                f"**Selector:** {selector_display}\n\n"
                "The bot will now monitor this product and notify when it comes "
                "back in stock! 🚀"
            )

            await interaction.response.send_message(response)
            logging.info(f"Added product '{name}' by {interaction.user}")
        else:
            await interaction.response.send_message(
                f"❌ Product '{name}' already exists in the monitoring list!",
                ephemeral=True,
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
    @discord.app_commands.choices(
        strategy=[
            discord.app_commands.Choice(name=s, value=s) for s in VALID_STRATEGIES
        ]
    )
    async def add_product(
        interaction: discord.Interaction,
        name: str,
        url: str,
        strategy: str = "auto",
        selector: str = "",
    ):
        await bot_commands.add_product(
            interaction, name, url, strategy=strategy, selector=selector
        )

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
