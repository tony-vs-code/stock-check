"""SQLite database persistence layer for UB Stock Bot."""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


class DuplicateProductError(ValueError):
    """Raised when attempting to create or rename a product to a duplicate name."""


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    """Resolve the SQLite database path from explicit argument or environment."""
    if db_path:
        return db_path
    env_db_path = os.getenv("DB_PATH", "").strip()
    if env_db_path:
        return env_db_path
    data_dir = os.getenv("DATA_DIR", "data").strip() or "data"
    return os.path.join(data_dir, "ubstock.db")


def _resolve_products_file(products_file: Optional[str] = None) -> str:
    """Resolve the legacy products.json path for migration."""
    if products_file:
        return products_file
    data_dir = os.getenv("DATA_DIR", "data").strip() or "data"
    return os.path.join(data_dir, "products.json")


class Database:
    """Thread-safe SQLite database manager for settings, products, and history."""

    DEFAULT_SETTINGS: dict[str, str] = {
        "check_stock_interval": "15",
        "notifications_enabled": "true",
    }

    def __init__(
        self,
        db_path: Optional[str] = None,
        products_file: Optional[str] = None,
    ) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.products_file = _resolve_products_file(products_file)
        self._lock = threading.RLock()
        self.init_db()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a locked SQLite connection with automatic commit/rollback."""
        with self._lock:
            conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_db(self) -> None:
        """Create schema tables, seed defaults, and migrate products.json if needed."""
        parent_dir = os.path.dirname(os.path.abspath(self.db_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    selector TEXT NOT NULL,
                    in_stock INTEGER DEFAULT 0,
                    last_checked TIMESTAMP,
                    last_status_change TIMESTAMP,
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    product_name TEXT,
                    status TEXT,
                    notification_sent INTEGER DEFAULT 0,
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            self._seed_settings(conn)
            self._migrate_products_json(conn)

    def _seed_settings(self, conn: sqlite3.Connection) -> None:
        """Seed default settings and optional environment variables if absent."""
        now = utc_now_iso()
        existing = {
            row["key"] for row in conn.execute("SELECT key FROM settings").fetchall()
        }

        defaults = dict(self.DEFAULT_SETTINGS)
        env_interval = os.getenv("CHECK_STOCK_INTERVAL", "").strip()
        if env_interval.isdigit() and int(env_interval) > 0:
            defaults["check_stock_interval"] = env_interval

        for key, value in defaults.items():
            if key not in existing:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, now),
                )

        env_token = os.getenv("DISCORD_TOKEN", "").strip()
        if env_token and "discord_token" not in existing:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("discord_token", env_token, now),
            )

        env_channel = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if env_channel and "discord_channel_id" not in existing:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("discord_channel_id", env_channel, now),
            )

    def _migrate_products_json(self, conn: sqlite3.Connection) -> None:
        """Import products from legacy products.json if the products table is empty."""
        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count > 0 or not os.path.exists(self.products_file):
            return

        try:
            with open(self.products_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return
            items = json.loads(content)
            if not isinstance(items, list):
                return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Could not migrate %s into SQLite: %s", self.products_file, exc
            )
            return

        now = utc_now_iso()
        imported = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            selector = str(item.get("selector", "")).strip()
            if not (name and url and selector):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO products
                    (name, url, selector, in_stock, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (name, url, selector, now),
            )
            imported += 1

        if imported:
            logger.info(
                "Migrated %d product(s) from %s into SQLite",
                imported,
                self.products_file,
            )

    # ------------------------------------------------------------------
    # Settings operations
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a single setting value by key."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        """Insert or update a single setting."""
        now = utc_now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def update_settings(self, updates: dict[str, str]) -> None:
        """Update multiple settings atomically."""
        if not updates:
            return
        now = utc_now_iso()
        with self._connection() as conn:
            for key, value in updates.items():
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )

    def get_all_settings(self) -> dict[str, str]:
        """Return all settings as a dictionary."""
        with self._connection() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}

    # ------------------------------------------------------------------
    # Product operations
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a SQLite product row into a serializable dictionary."""
        return {
            "id": row["id"],
            "name": row["name"],
            "url": row["url"],
            "selector": row["selector"],
            "in_stock": bool(row["in_stock"]),
            "last_checked": row["last_checked"],
            "last_status_change": row["last_status_change"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
        }

    def list_products(self) -> list[dict[str, Any]]:
        """List all monitored products ordered by ID."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                ORDER BY id ASC
                """
            ).fetchall()
            return [self._row_to_product(row) for row in rows]

    def get_product(self, product_id: int) -> Optional[dict[str, Any]]:
        """Fetch a single product by ID."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                WHERE id = ?
                """,
                (product_id,),
            ).fetchone()
            return self._row_to_product(row) if row is not None else None

    def get_product_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """Fetch a single product by unique name."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
            return self._row_to_product(row) if row is not None else None

    def create_product(self, name: str, url: str, selector: str) -> dict[str, Any]:
        """Create a new product and return its full record."""
        now = utc_now_iso()
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO products (name, url, selector, in_stock, created_at)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (name, url, selector, now),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateProductError(
                    f"Product '{name}' already exists."
                ) from exc

            product_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                WHERE id = ?
                """,
                (product_id,),
            ).fetchone()
            return self._row_to_product(row)

    def update_product(
        self,
        product_id: int,
        name: Optional[str] = None,
        url: Optional[str] = None,
        selector: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Update an existing product's fields and return the updated record."""
        with self._connection() as conn:
            existing = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                WHERE id = ?
                """,
                (product_id,),
            ).fetchone()
            if existing is None:
                return None

            new_name = name if name is not None else existing["name"]
            new_url = url if url is not None else existing["url"]
            new_selector = selector if selector is not None else existing["selector"]

            try:
                conn.execute(
                    """
                    UPDATE products
                    SET name = ?, url = ?, selector = ?
                    WHERE id = ?
                    """,
                    (new_name, new_url, new_selector, product_id),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateProductError(
                    f"Product '{new_name}' already exists."
                ) from exc

            updated = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                WHERE id = ?
                """,
                (product_id,),
            ).fetchone()
            return self._row_to_product(updated)

    def delete_product(self, product_id: int) -> bool:
        """Delete a product by ID. Returns True if a row was removed."""
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            return cursor.rowcount > 0

    def delete_product_by_name(self, name: str) -> bool:
        """Delete a product by name. Returns True if a row was removed."""
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM products WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def update_product_check_result(
        self,
        product_id: int,
        in_stock: Optional[bool],
        error: Optional[str],
        checked_at: Optional[str] = None,
        status_changed: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Update stock check metadata for a product after a check attempt."""
        timestamp = checked_at or utc_now_iso()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id, in_stock, last_status_change FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if existing is None:
                return None

            stock_val = existing["in_stock"] if in_stock is None else int(in_stock)
            change_ts = timestamp if status_changed else existing["last_status_change"]

            conn.execute(
                """
                UPDATE products
                SET in_stock = ?,
                    last_checked = ?,
                    last_status_change = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (stock_val, timestamp, change_ts, error, product_id),
            )
            row = conn.execute(
                """
                SELECT id, name, url, selector, in_stock, last_checked,
                       last_status_change, last_error, created_at
                FROM products
                WHERE id = ?
                """,
                (product_id,),
            ).fetchone()
            return self._row_to_product(row)

    # ------------------------------------------------------------------
    # History operations
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_history(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a SQLite history row into a serializable dictionary."""
        return {
            "id": row["id"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "status": row["status"],
            "notification_sent": bool(row["notification_sent"]),
            "message": row["message"],
            "created_at": row["created_at"],
        }

    def add_history(
        self,
        product_id: Optional[int],
        product_name: str,
        status: str,
        notification_sent: bool = False,
        message: str = "",
    ) -> dict[str, Any]:
        """Insert a history event and return the created record."""
        now = utc_now_iso()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO history
                    (product_id, product_name, status, notification_sent, message,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    product_name,
                    status,
                    int(notification_sent),
                    message,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT id, product_id, product_name, status, notification_sent,
                       message, created_at
                FROM history
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            return self._row_to_history(row)

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent history events ordered newest first."""
        safe_limit = max(1, min(limit, 500))
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, product_id, product_name, status, notification_sent,
                       message, created_at
                FROM history
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            return [self._row_to_history(row) for row in rows]

    def clear_history(self) -> int:
        """Delete all history records and return the number of deleted rows."""
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM history")
            return cursor.rowcount
