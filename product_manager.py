"""Product management module for UB Stock Bot."""

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

from config import DATA_DIR, PRODUCTS_FILE


@dataclass
class Product:
    """Represents a product to monitor for stock availability."""

    name: str
    url: str
    selector: str
    in_stock: bool = False

    def to_dict(self) -> Dict[str, str]:
        """Convert product to dictionary for JSON serialization."""
        return {"name": self.name, "url": self.url, "selector": self.selector}

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "Product":
        """Create product from dictionary."""
        return cls(name=data["name"], url=data["url"], selector=data["selector"])


class ProductManager:
    """Manages product persistence and operations."""

    def __init__(self):
        self.products: Dict[str, Product] = {}
        self.load_products()

    def load_products(self) -> None:
        """Load products from JSON file."""
        os.makedirs(DATA_DIR, exist_ok=True)

        if not os.path.exists(PRODUCTS_FILE):
            logging.info(
                f"No products file found. Creating empty file at {PRODUCTS_FILE}"
            )
            self.save_products()
            return

        try:
            with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    logging.info("Products file is empty. Creating empty list.")
                    return

                products_data = json.loads(content)
                self.products = {
                    item["name"]: Product.from_dict(item) for item in products_data
                }
                logging.info(f"Loaded {len(self.products)} products")
        except (json.JSONDecodeError, KeyError) as e:
            logging.error(f"Error loading products: {e}")
            self.products = {}
            self.save_products()

    def save_products(self) -> None:
        """Save products to JSON file."""
        products_list = [product.to_dict() for product in self.products.values()]

        try:
            with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
                json.dump(products_list, f, indent=2)
            logging.info(f"Saved {len(products_list)} products")
        except Exception as e:
            logging.error(f"Error saving products: {e}")

    def add_product(self, name: str, url: str, selector: str) -> bool:
        """Add a new product. Returns True if successful, False if already exists."""
        if name in self.products:
            return False

        self.products[name] = Product(name=name, url=url, selector=selector)
        self.save_products()
        return True

    def remove_product(self, name: str) -> bool:
        """Remove a product. Returns True if successful, False if not found."""
        if name not in self.products:
            return False

        del self.products[name]
        self.save_products()
        return True

    def get_product(self, name: str) -> Optional[Product]:
        """Get a product by name."""
        return self.products.get(name)

    def get_all_products(self) -> Dict[str, Product]:
        """Get all products."""
        return self.products.copy()

    def is_empty(self) -> bool:
        """Check if no products are being monitored."""
        return len(self.products) == 0

    def get_product_list_display(self) -> str:
        """Get formatted list of products for display."""
        if not self.products:
            return "No products are currently being monitored."

        product_list = "\n".join(
            [
                f"• **{name}**: <{product.url}>"
                for name, product in self.products.items()
            ]
        )
        return (
            f"**Currently Monitoring ({len(self.products)} products):**\n{product_list}"
        )
