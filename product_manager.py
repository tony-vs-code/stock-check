"""Product management module for UB Stock Bot."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from database import Database, DuplicateProductError


@dataclass
class Product:
    """Represents a product to monitor for stock availability."""

    name: str
    url: str
    selector: str
    in_stock: bool = False
    id: Optional[int] = None
    last_checked: Optional[str] = None
    last_status_change: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert product to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "selector": self.selector,
            "in_stock": self.in_stock,
            "last_checked": self.last_checked,
            "last_status_change": self.last_status_change,
            "last_error": self.last_error,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Product":
        """Create product from dictionary."""
        return cls(
            id=data.get("id"),
            name=data["name"],
            url=data["url"],
            selector=data["selector"],
            in_stock=bool(data.get("in_stock", False)),
            last_checked=data.get("last_checked"),
            last_status_change=data.get("last_status_change"),
            last_error=data.get("last_error"),
            created_at=data.get("created_at"),
        )


class ProductManager:
    """Manages product persistence and operations backed by SQLite."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or Database()

    def add_product(self, name: str, url: str, selector: str) -> bool:
        """Add a new product. Returns True if successful, False if already exists."""
        try:
            self.db.create_product(name=name, url=url, selector=selector)
            return True
        except DuplicateProductError:
            return False

    def remove_product(self, name: str) -> bool:
        """Remove a product by name. Returns True if successful, False if not found."""
        return self.db.delete_product_by_name(name)

    def get_product(self, name: str) -> Optional[Product]:
        """Get a product by name."""
        row = self.db.get_product_by_name(name)
        return Product.from_dict(row) if row else None

    def get_all_products(self) -> Dict[str, Product]:
        """Get all products keyed by name."""
        return {row["name"]: Product.from_dict(row) for row in self.db.list_products()}

    def is_empty(self) -> bool:
        """Check if no products are being monitored."""
        return len(self.db.list_products()) == 0

    def get_product_list_display(self) -> str:
        """Get formatted list of products for Discord display."""
        products = self.get_all_products()
        if not products:
            return "No products are currently being monitored."

        product_list = "\n".join(
            [f"• **{name}**: <{product.url}>" for name, product in products.items()]
        )
        return f"**Currently Monitoring ({len(products)} products):**\n{product_list}"
