"""Stock checking, multi-strategy detection, Discord alerts, and monitor loop."""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Tuple, Union
from urllib.parse import urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup, Tag

from config import DEFAULT_SELECTOR
from database import Database, utc_now_iso
from product_manager import Product
from utils import DEFAULT_HEADERS, fetch_product_page

if TYPE_CHECKING:
    import discord

    from discord_client import DiscordManager

logger = logging.getLogger(__name__)

VALID_STRATEGIES: tuple[str, ...] = (
    "auto",
    "json_ld",
    "shopify",
    "text_heuristic",
    "css_selector",
)

_SHOPIFY_HTML_MARKERS: tuple[str, ...] = (
    "cdn.shopify.com",
    "shopify.shop",
    "shopify-section",
    "shopify-features",
    "window.shopify",
    'content="shopify"',
    "content='shopify'",
)

_JSON_LD_IN_STOCK = {
    "instock",
    "preorder",
    "presale",
    "limitedavailability",
    "onlineonly",
}
_JSON_LD_OUT_OF_STOCK = {
    "outofstock",
    "soldout",
    "discontinued",
    "backorder",
    "instoreonly",
}

_META_IN_STOCK = {
    "instock",
    "available",
    "preorder",
    "availablefororder",
    "yes",
    "true",
}
_META_OUT_OF_STOCK = {
    "outofstock",
    "oos",
    "soldout",
    "unavailable",
    "discontinued",
    "backorder",
    "no",
    "false",
}

_NEGATIVE_PHRASES: tuple[str, ...] = (
    "sold out",
    "out of stock",
    "currently unavailable",
    "backorder",
    "not in stock",
)
_POSITIVE_BUTTON_PHRASES: tuple[str, ...] = (
    "add to cart",
    "add to bag",
    "add to basket",
    "buy now",
    "pre-order",
    "preorder",
)
_POSITIVE_TEXT_PHRASES: tuple[str, ...] = (
    "add to cart",
    "buy now",
    "in stock",
)
_IGNORED_TEXT_PARENTS = {"script", "style", "noscript", "template"}


def _build_shopify_json_url(url: str) -> str:
    """Convert a product page URL into its Shopify .json endpoint URL."""
    parsed = urlparse(url.strip())
    clean_path = parsed.path.rstrip("/")
    if not clean_path.endswith(".json"):
        clean_path = f"{clean_path}.json" if clean_path else "/products.json"
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", "", ""))


async def fetch_shopify_json(
    url: str,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """Fetch JSON from a Shopify product .json endpoint."""
    json_url = _build_shopify_json_url(url)
    timeout = aiohttp.ClientTimeout(total=10)
    headers = {**DEFAULT_HEADERS, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(json_url) as response:
                if response.status != 200:
                    return None, (
                        f"Shopify JSON endpoint returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
                if not isinstance(payload, dict):
                    return None, "Shopify JSON endpoint returned non-object payload."
                return payload, None
    except Exception as exc:
        return None, f"Failed to fetch Shopify JSON: {exc}"


def _make_result(
    *,
    in_stock: bool,
    found_selector: bool,
    strategy_used: str,
    detected_by: str,
    error: Optional[str] = None,
    details: Optional[str] = None,
) -> dict[str, Any]:
    """Build a uniform structured detection result dictionary."""
    return {
        "in_stock": in_stock,
        "found_selector": found_selector,
        "strategy_used": strategy_used,
        "detected_by": detected_by,
        "error": error,
        "details": details,
    }


def _normalize_token(value: str) -> str:
    """Normalize Schema.org / meta availability strings for comparison."""
    tail = value.strip().rsplit("/", 1)[-1]
    return re.sub(r"[\s_\-]+", "", tail.lower())


def _is_element_disabled(element: Tag) -> bool:
    """Return True if an HTML element is disabled via attribute or ARIA state."""
    if element.has_attr("disabled"):
        return True
    if str(element.get("aria-disabled", "")).strip().lower() == "true":
        return True
    if str(element.get("data-disabled", "")).strip().lower() == "true":
        return True
    return False


def _has_ignored_ancestor(tag: Optional[Tag]) -> bool:
    """Return True if tag or any parent is script/style/noscript/template."""
    current = tag
    while current is not None:
        if getattr(current, "name", None) in _IGNORED_TEXT_PARENTS:
            return True
        current = current.parent
    return False


def _collect_json_ld_availabilities(node: Any, out: list[str]) -> None:
    """Recursively collect Schema.org availability values from JSON-LD objects."""
    if isinstance(node, list):
        for item in node:
            _collect_json_ld_availabilities(item, out)
        return
    if not isinstance(node, dict):
        return

    raw_type = node.get("@type")
    if isinstance(raw_type, list):
        types = {str(t).lower() for t in raw_type}
    elif isinstance(raw_type, str):
        types = {raw_type.lower()}
    else:
        types = set()

    is_commerce_node = bool(
        types & {"product", "offer", "aggregateoffer", "productmodel"}
    )
    if (is_commerce_node or not types) and isinstance(node.get("availability"), str):
        out.append(node["availability"])

    for key in ("offers", "@graph", "mainEntity", "itemListElement", "item"):
        if key in node:
            _collect_json_ld_availabilities(node[key], out)


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
    """Multi-strategy stock detection engine and selector utilities."""

    @staticmethod
    def parse_stock_status(content: str, selector: str) -> bool:
        """Parse HTML content to check CSS selector presence and enabled state."""
        soup = BeautifulSoup(content, "html.parser")
        matches = soup.select(selector)
        return any(not _is_element_disabled(el) for el in matches)

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
    def validate_strategy(strategy: Optional[str]) -> Optional[str]:
        """Validate a detection strategy name. Returns error message or None."""
        cleaned = (strategy or "auto").strip().lower()
        if cleaned not in VALID_STRATEGIES:
            allowed = ", ".join(VALID_STRATEGIES)
            return f"Unsupported strategy '{strategy}'. Must be one of: {allowed}."
        return None

    @staticmethod
    def _is_shopify_page(url: str, content: str) -> bool:
        """Return True when URL or HTML content indicates a Shopify storefront."""
        lower_url = url.lower()
        if (
            lower_url.rstrip("/").endswith(".json")
            or "myshopify.com" in lower_url
            or "shopify" in lower_url
        ):
            return True
        lower_content = content.lower()
        return any(marker in lower_content for marker in _SHOPIFY_HTML_MARKERS)

    @staticmethod
    def _evaluate_shopify_payload(data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Evaluate Shopify product JSON variants for availability."""
        variants = None
        product_obj = data.get("product")
        if isinstance(product_obj, dict) and isinstance(
            product_obj.get("variants"), list
        ):
            variants = product_obj["variants"]
        elif isinstance(data.get("variants"), list):
            variants = data["variants"]

        if not variants:
            return None

        available_count = sum(
            1
            for variant in variants
            if isinstance(variant, dict) and variant.get("available") is True
        )
        in_stock = available_count > 0
        return _make_result(
            in_stock=in_stock,
            found_selector=in_stock,
            strategy_used="shopify",
            detected_by="shopify_json",
            details=f"{available_count} of {len(variants)} variant(s) available",
        )

    @staticmethod
    def _parse_json_ld(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        """Inspect Schema.org JSON-LD scripts for Product/Offer availability."""
        availabilities: list[str] = []
        for script in soup.find_all("script"):
            script_type = str(script.get("type", "")).lower()
            if "ld+json" not in script_type:
                continue
            raw_json = (script.string or script.get_text() or "").strip()
            if not raw_json:
                continue
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError:
                continue
            _collect_json_ld_availabilities(parsed, availabilities)

        if not availabilities:
            return None

        in_stock_matches: list[str] = []
        out_stock_matches: list[str] = []
        for raw_val in availabilities:
            token = _normalize_token(raw_val)
            if token in _JSON_LD_IN_STOCK:
                in_stock_matches.append(raw_val.rsplit("/", 1)[-1])
            elif token in _JSON_LD_OUT_OF_STOCK:
                out_stock_matches.append(raw_val.rsplit("/", 1)[-1])

        if in_stock_matches:
            return _make_result(
                in_stock=True,
                found_selector=True,
                strategy_used="json_ld",
                detected_by="json_ld_offer",
                details=f"Offer availability: {', '.join(in_stock_matches[:3])}",
            )
        if out_stock_matches:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="json_ld",
                detected_by="json_ld_offer",
                details=f"Offer availability: {', '.join(out_stock_matches[:3])}",
            )
        return None

    @staticmethod
    def _parse_meta_tags(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        """Inspect OpenGraph / product meta and link availability tags."""
        candidates: list[tuple[str, str]] = []
        for meta in soup.find_all("meta"):
            key = (
                str(
                    meta.get("property")
                    or meta.get("name")
                    or meta.get("itemprop")
                    or ""
                )
                .strip()
                .lower()
            )
            if key in ("og:availability", "product:availability", "availability"):
                content = str(meta.get("content") or "").strip()
                if content:
                    candidates.append((key, content))

        for link in soup.find_all("link"):
            itemprop = str(link.get("itemprop") or "").strip().lower()
            if itemprop == "availability":
                val = str(link.get("href") or link.get("content") or "").strip()
                if val:
                    candidates.append(("itemprop:availability", val))

        for key, raw_val in candidates:
            token = _normalize_token(raw_val)
            if token in _META_IN_STOCK:
                return _make_result(
                    in_stock=True,
                    found_selector=True,
                    strategy_used="meta_tags",
                    detected_by=key,
                    details=f"Meta {key}: {raw_val}",
                )
            if token in _META_OUT_OF_STOCK:
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="meta_tags",
                    detected_by=key,
                    details=f"Meta {key}: {raw_val}",
                )
        return None

    @staticmethod
    def _parse_text_heuristic(soup: BeautifulSoup) -> Optional[dict[str, Any]]:
        """Analyze interactive buttons and visible text for stock signals."""
        action_selector = (
            "button, input[type='submit'], input[type='button'], "
            "[role='button'], a.btn, a.button"
        )
        enabled_positive: Optional[str] = None
        disabled_positive: Optional[str] = None
        sold_out_button: Optional[str] = None

        for el in soup.select(action_selector):
            if _has_ignored_ancestor(el):
                continue
            parts = [
                el.get_text(" ", strip=True),
                str(el.get("value") or ""),
                str(el.get("aria-label") or ""),
                str(el.get("label") or ""),
            ]
            label = " ".join(p for p in parts if p).strip().lower()
            if not label:
                continue

            for phrase in _NEGATIVE_PHRASES:
                if phrase in label and sold_out_button is None:
                    sold_out_button = phrase
                    break

            for phrase in _POSITIVE_BUTTON_PHRASES:
                if phrase in label:
                    if _is_element_disabled(el):
                        if disabled_positive is None:
                            disabled_positive = phrase
                    else:
                        if enabled_positive is None:
                            enabled_positive = phrase
                    break

        if enabled_positive is not None:
            return _make_result(
                in_stock=True,
                found_selector=True,
                strategy_used="text_heuristic",
                detected_by="button_enabled",
                details=f"Found enabled '{enabled_positive}' button",
            )
        if disabled_positive is not None:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="text_heuristic",
                detected_by="button_disabled",
                details=f"Matching '{disabled_positive}' button is disabled",
            )
        if sold_out_button is not None:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="text_heuristic",
                detected_by="sold_out_button",
                details=f"Found '{sold_out_button}' button",
            )

        root = soup.body or soup
        visible_chunks = [
            text.strip()
            for text in root.find_all(string=True)
            if text.strip() and not _has_ignored_ancestor(text.parent)
        ]
        visible_text = re.sub(r"\s+", " ", " ".join(visible_chunks)).lower()

        for phrase in _NEGATIVE_PHRASES:
            if re.search(rf"\b{re.escape(phrase)}\b", visible_text):
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="text_heuristic",
                    detected_by="sold_out_text",
                    details=f"Matched sold-out text: '{phrase}'",
                )

        for phrase in _POSITIVE_TEXT_PHRASES:
            if re.search(rf"\b{re.escape(phrase)}\b", visible_text):
                return _make_result(
                    in_stock=True,
                    found_selector=True,
                    strategy_used="text_heuristic",
                    detected_by="in_stock_text",
                    details=f"Matched positive text: '{phrase}'",
                )

        return None

    @staticmethod
    def _parse_css_selector(
        soup: BeautifulSoup, selector: Optional[str]
    ) -> dict[str, Any]:
        """Evaluate CSS selector matches including disabled attribute inspection."""
        effective_selector = (selector or "").strip() or DEFAULT_SELECTOR
        selector_error = StockChecker.validate_selector(effective_selector)
        if selector_error:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="css_selector",
                detected_by="invalid_selector",
                error=selector_error,
                details=selector_error,
            )

        matches = soup.select(effective_selector)
        if not matches:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="css_selector",
                detected_by="css_selector_not_found",
                details=f"Selector '{effective_selector}' did not match any element.",
            )

        if any(not _is_element_disabled(el) for el in matches):
            return _make_result(
                in_stock=True,
                found_selector=True,
                strategy_used="css_selector",
                detected_by="css_selector",
                details=f"Matched enabled element for '{effective_selector}'.",
            )

        return _make_result(
            in_stock=False,
            found_selector=True,
            strategy_used="css_selector",
            detected_by="css_selector_disabled",
            details=f"Matched element for '{effective_selector}' is disabled.",
        )

    @staticmethod
    async def detect_stock(
        url: str,
        selector: Optional[str] = None,
        strategy: str = "auto",
    ) -> dict[str, Any]:
        """Run the requested stock detection strategy against a product URL."""
        strategy_error = StockChecker.validate_strategy(strategy)
        normalized_strategy = (strategy or "auto").strip().lower()
        if strategy_error:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used=normalized_strategy,
                detected_by="invalid_strategy",
                error=strategy_error,
                details=strategy_error,
            )

        if normalized_strategy == "css_selector":
            effective_selector = (selector or "").strip() or DEFAULT_SELECTOR
            selector_error = StockChecker.validate_selector(effective_selector)
            if selector_error:
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="css_selector",
                    detected_by="invalid_selector",
                    error=selector_error,
                    details=selector_error,
                )
        elif normalized_strategy == "auto" and selector and selector.strip():
            selector_error = StockChecker.validate_selector(selector.strip())
            if selector_error:
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="css_selector",
                    detected_by="invalid_selector",
                    error=selector_error,
                    details=selector_error,
                )

        if normalized_strategy == "shopify":
            data, err = await fetch_shopify_json(url)
            if err:
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="shopify",
                    detected_by="shopify_json_error",
                    error=err,
                    details=err,
                )
            assert data is not None
            shopify_res = StockChecker._evaluate_shopify_payload(data)
            if shopify_res is not None:
                return shopify_res
            msg = "No product variants found in Shopify JSON."
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="shopify",
                detected_by="shopify_json_not_found",
                error=msg,
                details=msg,
            )

        try:
            content = await fetch_product_page(url)
        except Exception as exc:
            err_msg = str(exc)
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used=normalized_strategy,
                detected_by="fetch_error",
                error=err_msg,
                details=err_msg,
            )

        if normalized_strategy == "auto":
            stripped = content.lstrip()
            if stripped.startswith("{"):
                try:
                    inline_json = json.loads(stripped)
                    if isinstance(inline_json, dict):
                        shopify_res = StockChecker._evaluate_shopify_payload(
                            inline_json
                        )
                        if shopify_res is not None:
                            return shopify_res
                except json.JSONDecodeError:
                    pass

            if StockChecker._is_shopify_page(url, content):
                data, _ = await fetch_shopify_json(url)
                if data is not None:
                    shopify_res = StockChecker._evaluate_shopify_payload(data)
                    if shopify_res is not None:
                        return shopify_res

        soup = BeautifulSoup(content, "html.parser")

        if normalized_strategy == "json_ld":
            res = StockChecker._parse_json_ld(soup)
            if res is not None:
                return res
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="json_ld",
                detected_by="json_ld_not_found",
                details="No Schema.org Product/Offer availability metadata found.",
            )

        if normalized_strategy == "text_heuristic":
            res = StockChecker._parse_text_heuristic(soup)
            if res is not None:
                return res
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="text_heuristic",
                detected_by="text_heuristic_inconclusive",
                details="No definitive stock text or action button found.",
            )

        if normalized_strategy == "css_selector":
            return StockChecker._parse_css_selector(soup, selector)

        # Auto strategy cascade: JSON-LD -> Meta/OpenGraph -> Text/Buttons -> CSS
        for detector in (
            StockChecker._parse_json_ld,
            StockChecker._parse_meta_tags,
            StockChecker._parse_text_heuristic,
        ):
            res = detector(soup)
            if res is not None:
                return res

        return StockChecker._parse_css_selector(soup, selector)

    @staticmethod
    async def check_product(
        product: Union[Product, dict[str, Any]],
    ) -> dict[str, Any]:
        """Check stock for a product using its configured strategy."""
        name = product["name"] if isinstance(product, dict) else product.name
        url = product["url"] if isinstance(product, dict) else product.url
        selector = (
            product.get("selector", "")
            if isinstance(product, dict)
            else product.selector
        )
        strategy = (
            product.get("strategy", "auto")
            if isinstance(product, dict)
            else product.strategy
        )

        result = await StockChecker.detect_stock(
            url=url,
            selector=selector,
            strategy=strategy or "auto",
        )
        if result["error"]:
            logger.error("Error checking stock for %s: %s", name, result["error"])
        return result

    @staticmethod
    async def check_single_product(
        product: Union[Product, dict[str, Any]],
    ) -> Tuple[bool, Optional[str]]:
        """Check stock for a single product. Returns (in_stock, error_message)."""
        result = await StockChecker.check_product(product)
        return bool(result["in_stock"]), result["error"]

    @staticmethod
    async def test_selector(
        url: str,
        selector: Optional[str] = None,
        strategy: Optional[str] = "auto",
    ) -> dict[str, Any]:
        """Test a URL with a detection strategy and optional CSS selector."""
        return await StockChecker.detect_stock(
            url=url,
            selector=selector,
            strategy=strategy or "auto",
        )

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
        result = await StockChecker.check_product(product)
        is_in_stock = bool(result["in_stock"])
        error = result["error"]
        strategy_used = result["strategy_used"]
        detected_by = result["detected_by"]
        details = result["details"]
        strategy_details = (
            f"{strategy_used} ({detected_by}): {details}"
            if details
            else f"{strategy_used} ({detected_by})"
        )
        checked_at = utc_now_iso()

        if error is not None:
            updated = self.db.update_product_check_result(
                product_id=product["id"],
                in_stock=None,
                error=error,
                checked_at=checked_at,
                status_changed=False,
                strategy_details=strategy_details,
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
            strategy_details=strategy_details,
        )

        if is_in_stock and not was_in_stock:
            sent, notify_err = await self._send_in_stock_alert(product)
            msg = f"{product['name']} is back in stock! [{strategy_used}]"
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
                message=f"{product['name']} went out of stock. [{strategy_used}]",
            )
        elif record_unchanged:
            status_str = "in_stock" if is_in_stock else "out_of_stock"
            state_label = "in stock" if is_in_stock else "out of stock"
            self.db.add_history(
                product_id=product["id"],
                product_name=product["name"],
                status=status_str,
                notification_sent=False,
                message=(
                    f"Manual check: {product['name']} is {state_label} "
                    f"[{strategy_used}]."
                ),
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
