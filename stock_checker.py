"""Stock checking, multi-strategy detection, Discord alerts, and monitor loop."""

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Tuple, TypedDict, Union
from urllib.parse import urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup, Tag

from config import DEFAULT_SELECTOR
from database import Database, utc_now_iso
from notifications import (
    build_webhook_payload,
    format_price,
    send_discord_rest_message,
    send_telegram_notification,
    send_webhook_notification,
)
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


class StockResult(TypedDict):
    """Structured result returned by stock detection checks."""

    in_stock: bool
    found_selector: bool
    strategy_used: str
    detected_by: str
    error: Optional[str]
    details: Optional[str]
    price: Optional[float]
    currency: str


class ProxyRotator:
    """Round-robin proxy rotator parsing newline- or comma-separated proxy lists."""

    def __init__(self) -> None:
        self._raw_list: str = ""
        self._proxies: list[str] = []
        self._index: int = 0

    @staticmethod
    def parse_proxies(raw_proxy_list: str) -> list[str]:
        """Parse newline- or comma-separated proxy URLs into a clean list."""
        if not raw_proxy_list:
            return []
        tokens = re.split(r"[\r\n,]+", raw_proxy_list)
        return [token.strip() for token in tokens if token.strip()]

    def next_proxy(self, enabled: bool, raw_proxy_list: str) -> Optional[str]:
        """Return the next proxy URL round-robin if enabled and proxies exist."""
        if not enabled:
            return None
        if raw_proxy_list != self._raw_list:
            self._raw_list = raw_proxy_list
            self._proxies = self.parse_proxies(raw_proxy_list)
            self._index = 0
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index = (self._index + 1) % len(self._proxies)
        return proxy


class DomainRateLimiter:
    """Per-domain rate limiter tracking last request timestamps."""

    def __init__(self) -> None:
        self.last_domain_request: dict[str, float] = {}
        self._domain_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract normalized hostname/netloc from a URL."""
        return urlparse(url.strip()).netloc.lower()

    async def wait_if_needed(self, url: str, delay: float) -> float:
        """Sleep if a request to the same domain occurred within `delay` seconds."""
        domain = self.extract_domain(url)
        if not domain:
            return 0.0
        if delay <= 0:
            self.last_domain_request[domain] = time.monotonic()
            return 0.0

        lock = self._domain_locks.setdefault(domain, asyncio.Lock())
        async with lock:
            slept = 0.0
            last_ts = self.last_domain_request.get(domain)
            if last_ts is not None:
                elapsed = time.monotonic() - last_ts
                if elapsed < delay:
                    slept = delay - elapsed
                    await asyncio.sleep(slept)
            self.last_domain_request[domain] = time.monotonic()
            return slept


_PRICE_REGEX = re.compile(
    r"(?P<symbol>[$€£])\s*"
    r"(?P<amount>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?)"
    r"(?!\d)"
)
_SYMBOL_TO_CURRENCY: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
}


def _parse_price_value(raw: Any) -> Optional[float]:
    """Parse a numeric price from int, float, or formatted string."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if (math.isfinite(val) and val >= 0.0) else None
    text = str(raw).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.,]", "", text)
    if not cleaned:
        return None
    # Remove thousands commas when followed by 3 digits or when dot decimal exists
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2 and len(parts) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        val = float(cleaned)
        return val if (math.isfinite(val) and val >= 0.0) else None
    except ValueError:
        return None


def _extract_price_from_text(text: str) -> Tuple[Optional[float], str]:
    """Extract first currency-prefixed price and currency code from plain text."""
    if not text:
        return None, "USD"
    match = _PRICE_REGEX.search(text)
    if not match:
        return None, "USD"
    symbol = match.group("symbol")
    amount = _parse_price_value(match.group("amount"))
    if amount is None:
        return None, "USD"
    return amount, _SYMBOL_TO_CURRENCY.get(symbol, "USD")


def _build_shopify_json_url(url: str) -> str:
    """Convert a product page URL into its Shopify .json endpoint URL."""
    parsed = urlparse(url.strip())
    clean_path = parsed.path.rstrip("/")
    if not clean_path.endswith(".json"):
        clean_path = f"{clean_path}.json" if clean_path else "/products.json"
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", "", ""))


async def fetch_shopify_json(
    url: str,
    proxy: Optional[str] = None,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """Fetch JSON from a Shopify product .json endpoint."""
    json_url = _build_shopify_json_url(url)
    timeout = aiohttp.ClientTimeout(total=10)
    headers = {**DEFAULT_HEADERS, "Accept": "application/json"}
    request_kwargs: dict[str, Any] = {"proxy": proxy} if proxy else {}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(json_url, **request_kwargs) as response:
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
    price: Optional[float] = None,
    currency: str = "USD",
) -> StockResult:
    """Build a uniform structured detection result dictionary."""
    return {
        "in_stock": in_stock,
        "found_selector": found_selector,
        "strategy_used": strategy_used,
        "detected_by": detected_by,
        "error": error,
        "details": details,
        "price": price,
        "currency": currency,
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


def _visible_text_from_soup(soup: BeautifulSoup) -> str:
    """Extract normalized visible text excluding script/style/template tags."""
    root = soup.body or soup
    visible_chunks = [
        text.strip()
        for text in root.find_all(string=True)
        if text.strip() and not _has_ignored_ancestor(text.parent)
    ]
    return re.sub(r"\s+", " ", " ".join(visible_chunks))


def _collect_json_ld_info(
    node: Any,
    availabilities: list[str],
    prices: list[Tuple[float, str]],
) -> None:
    """Recursively collect Schema.org availabilities and prices from JSON-LD."""
    if isinstance(node, list):
        for item in node:
            _collect_json_ld_info(item, availabilities, prices)
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
        types
        & {
            "product",
            "offer",
            "aggregateoffer",
            "productmodel",
            "pricespecification",
        }
    )
    if is_commerce_node or not types:
        if isinstance(node.get("availability"), str):
            availabilities.append(node["availability"])

        raw_price = node.get("price")
        if raw_price is None:
            raw_price = node.get("lowPrice")
        parsed_price = _parse_price_value(raw_price)
        if parsed_price is not None:
            curr = str(node.get("priceCurrency") or "USD").strip().upper() or "USD"
            prices.append((parsed_price, curr))

    for key in (
        "offers",
        "priceSpecification",
        "@graph",
        "mainEntity",
        "itemListElement",
        "item",
    ):
        if key in node:
            _collect_json_ld_info(node[key], availabilities, prices)


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
    def _evaluate_shopify_payload(data: dict[str, Any]) -> Optional[StockResult]:
        """Evaluate Shopify product JSON variants for availability and price."""
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

        price: Optional[float] = None
        currency = "USD"
        if isinstance(product_obj, dict) and product_obj.get("currency"):
            currency = str(product_obj["currency"]).strip().upper() or "USD"

        first_variant = variants[0] if isinstance(variants[0], dict) else None
        if first_variant is not None:
            price = _parse_price_value(first_variant.get("price"))
            if first_variant.get("price_currency"):
                currency = str(first_variant["price_currency"]).strip().upper() or "USD"
        if price is None:
            for variant in variants:
                if isinstance(variant, dict):
                    candidate = _parse_price_value(variant.get("price"))
                    if candidate is not None:
                        price = candidate
                        if variant.get("price_currency"):
                            currency = (
                                str(variant["price_currency"]).strip().upper() or "USD"
                            )
                        break

        return _make_result(
            in_stock=in_stock,
            found_selector=in_stock,
            strategy_used="shopify",
            detected_by="shopify_json",
            details=f"{available_count} of {len(variants)} variant(s) available",
            price=price,
            currency=currency,
        )

    @staticmethod
    def _extract_price_from_meta(soup: BeautifulSoup) -> Tuple[Optional[float], str]:
        """Extract product price and currency from OpenGraph / meta tags."""
        price: Optional[float] = None
        currency = "USD"
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
            content = str(meta.get("content") or "").strip()
            if not content:
                continue
            if key in ("product:price:amount", "og:price:amount", "price"):
                if price is None:
                    price = _parse_price_value(content)
            elif key in (
                "product:price:currency",
                "og:price:currency",
                "pricecurrency",
            ):
                currency = content.upper() or "USD"
        return price, currency

    @staticmethod
    def _extract_price_from_json_ld(
        soup: BeautifulSoup,
    ) -> Tuple[Optional[float], str]:
        """Extract first price and currency from JSON-LD scripts in soup."""
        availabilities: list[str] = []
        prices: list[Tuple[float, str]] = []
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
            _collect_json_ld_info(parsed, availabilities, prices)
        if prices:
            return prices[0]
        return None, "USD"

    @staticmethod
    def _extract_price_from_soup(soup: BeautifulSoup) -> Tuple[Optional[float], str]:
        """Extract product price and currency from JSON-LD, meta tags, or text."""
        price, currency = StockChecker._extract_price_from_json_ld(soup)
        if price is not None:
            return price, currency
        price, currency = StockChecker._extract_price_from_meta(soup)
        if price is not None:
            return price, currency
        return _extract_price_from_text(_visible_text_from_soup(soup))

    @staticmethod
    def _parse_json_ld(soup: BeautifulSoup) -> Optional[StockResult]:
        """Inspect Schema.org JSON-LD scripts for Product/Offer availability."""
        availabilities: list[str] = []
        prices: list[Tuple[float, str]] = []
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
            _collect_json_ld_info(parsed, availabilities, prices)

        if not availabilities:
            return None

        price, currency = prices[0] if prices else (None, "USD")

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
                price=price,
                currency=currency,
            )
        if out_stock_matches:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="json_ld",
                detected_by="json_ld_offer",
                details=f"Offer availability: {', '.join(out_stock_matches[:3])}",
                price=price,
                currency=currency,
            )
        return None

    @staticmethod
    def _parse_meta_tags(soup: BeautifulSoup) -> Optional[StockResult]:
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

        price, currency = StockChecker._extract_price_from_meta(soup)

        for key, raw_val in candidates:
            token = _normalize_token(raw_val)
            if token in _META_IN_STOCK:
                return _make_result(
                    in_stock=True,
                    found_selector=True,
                    strategy_used="meta_tags",
                    detected_by=key,
                    details=f"Meta {key}: {raw_val}",
                    price=price,
                    currency=currency,
                )
            if token in _META_OUT_OF_STOCK:
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="meta_tags",
                    detected_by=key,
                    details=f"Meta {key}: {raw_val}",
                    price=price,
                    currency=currency,
                )
        return None

    @staticmethod
    def _parse_text_heuristic(soup: BeautifulSoup) -> Optional[StockResult]:
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

        raw_visible_text = _visible_text_from_soup(soup)
        price, currency = _extract_price_from_text(raw_visible_text)

        if enabled_positive is not None:
            return _make_result(
                in_stock=True,
                found_selector=True,
                strategy_used="text_heuristic",
                detected_by="button_enabled",
                details=f"Found enabled '{enabled_positive}' button",
                price=price,
                currency=currency,
            )
        if disabled_positive is not None:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="text_heuristic",
                detected_by="button_disabled",
                details=f"Matching '{disabled_positive}' button is disabled",
                price=price,
                currency=currency,
            )
        if sold_out_button is not None:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="text_heuristic",
                detected_by="sold_out_button",
                details=f"Found '{sold_out_button}' button",
                price=price,
                currency=currency,
            )

        visible_text = raw_visible_text.lower()

        for phrase in _NEGATIVE_PHRASES:
            if re.search(rf"\b{re.escape(phrase)}\b", visible_text):
                return _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="text_heuristic",
                    detected_by="sold_out_text",
                    details=f"Matched sold-out text: '{phrase}'",
                    price=price,
                    currency=currency,
                )

        for phrase in _POSITIVE_TEXT_PHRASES:
            if re.search(rf"\b{re.escape(phrase)}\b", visible_text):
                return _make_result(
                    in_stock=True,
                    found_selector=True,
                    strategy_used="text_heuristic",
                    detected_by="in_stock_text",
                    details=f"Matched positive text: '{phrase}'",
                    price=price,
                    currency=currency,
                )

        return None

    @staticmethod
    def _parse_css_selector(
        soup: BeautifulSoup, selector: Optional[str]
    ) -> StockResult:
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
        price: Optional[float] = None
        currency = "USD"
        for el in matches:
            el_price, el_currency = _extract_price_from_text(
                el.get_text(" ", strip=True)
            )
            if el_price is not None:
                price, currency = el_price, el_currency
                break
        if price is None:
            price, currency = _extract_price_from_text(_visible_text_from_soup(soup))

        if not matches:
            return _make_result(
                in_stock=False,
                found_selector=False,
                strategy_used="css_selector",
                detected_by="css_selector_not_found",
                details=f"Selector '{effective_selector}' did not match any element.",
                price=price,
                currency=currency,
            )

        if any(not _is_element_disabled(el) for el in matches):
            return _make_result(
                in_stock=True,
                found_selector=True,
                strategy_used="css_selector",
                detected_by="css_selector",
                details=f"Matched enabled element for '{effective_selector}'.",
                price=price,
                currency=currency,
            )

        return _make_result(
            in_stock=False,
            found_selector=True,
            strategy_used="css_selector",
            detected_by="css_selector_disabled",
            details=f"Matched element for '{effective_selector}' is disabled.",
            price=price,
            currency=currency,
        )

    @staticmethod
    async def detect_stock(
        url: str,
        selector: Optional[str] = None,
        strategy: str = "auto",
        proxy: Optional[str] = None,
    ) -> StockResult:
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
            if proxy:
                data, err = await fetch_shopify_json(url, proxy=proxy)
            else:
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
            if proxy:
                content = await fetch_product_page(url, proxy=proxy)
            else:
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
                if proxy:
                    data, _ = await fetch_shopify_json(url, proxy=proxy)
                else:
                    data, _ = await fetch_shopify_json(url)
                if data is not None:
                    shopify_res = StockChecker._evaluate_shopify_payload(data)
                    if shopify_res is not None:
                        return shopify_res

        soup = BeautifulSoup(content, "html.parser")

        result: Optional[StockResult] = None
        if normalized_strategy == "json_ld":
            result = StockChecker._parse_json_ld(soup)
            if result is None:
                result = _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="json_ld",
                    detected_by="json_ld_not_found",
                    details="No Schema.org Product/Offer availability metadata found.",
                )
        elif normalized_strategy == "text_heuristic":
            result = StockChecker._parse_text_heuristic(soup)
            if result is None:
                result = _make_result(
                    in_stock=False,
                    found_selector=False,
                    strategy_used="text_heuristic",
                    detected_by="text_heuristic_inconclusive",
                    details="No definitive stock text or action button found.",
                )
        elif normalized_strategy == "css_selector":
            result = StockChecker._parse_css_selector(soup, selector)
        else:
            # Auto strategy cascade: JSON-LD -> Meta/OpenGraph -> Text/Buttons -> CSS
            for detector in (
                StockChecker._parse_json_ld,
                StockChecker._parse_meta_tags,
                StockChecker._parse_text_heuristic,
            ):
                result = detector(soup)
                if result is not None:
                    break
            if result is None:
                result = StockChecker._parse_css_selector(soup, selector)

        if result["price"] is None:
            fallback_price, fallback_currency = StockChecker._extract_price_from_soup(
                soup
            )
            if fallback_price is not None:
                result["price"] = fallback_price
                result["currency"] = fallback_currency

        return result

    @staticmethod
    async def check_product(
        product: Union[Product, dict[str, Any]],
        proxy: Optional[str] = None,
    ) -> StockResult:
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
            proxy=proxy,
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
    """Periodic stock monitoring service backed by SQLite and multi-channel alerts."""

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
        self._rate_limiter = DomainRateLimiter()
        self._proxy_rotator = ProxyRotator()

    def _get_bool_setting(self, key: str, default: str = "false") -> bool:
        """Read a boolean-like setting value from DB."""
        raw = (self.db.get_setting(key, default) or default).strip().lower()
        return raw in ("true", "1", "yes", "on")

    def get_check_interval(self) -> int:
        """Read the check interval (seconds) from DB settings."""
        raw = self.db.get_setting("check_stock_interval", "15") or "15"
        try:
            value = int(raw)
            return max(1, value)
        except ValueError:
            return 15

    def get_domain_rate_limit_delay(self) -> float:
        """Read per-domain rate limit delay (seconds) from DB settings."""
        raw = self.db.get_setting("domain_rate_limit_delay", "1.0") or "1.0"
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 1.0

    def notifications_enabled(self) -> bool:
        """Return whether stock notifications are enabled in DB settings."""
        return self._get_bool_setting("notifications_enabled", "true")

    def price_alerts_enabled(self) -> bool:
        """Return whether price drop alerts are enabled in DB settings."""
        return self._get_bool_setting("price_alerts_enabled", "true")

    def telegram_enabled(self) -> bool:
        """Return whether Telegram notifications are enabled in DB settings."""
        return self._get_bool_setting("telegram_enabled", "false")

    def webhook_enabled(self) -> bool:
        """Return whether generic webhook notifications are enabled in DB settings."""
        return self._get_bool_setting("webhook_enabled", "false")

    def proxy_enabled(self) -> bool:
        """Return whether proxy rotation is enabled in DB settings."""
        return self._get_bool_setting("proxy_enabled", "false")

    def get_next_proxy(self) -> Optional[str]:
        """Return the next configured proxy URL if proxy rotation is enabled."""
        proxy_list = self.db.get_setting("proxy_list", "") or ""
        return self._proxy_rotator.next_proxy(self.proxy_enabled(), proxy_list)

    async def _send_discord_alert(
        self,
        message: str,
        embed: Optional[dict[str, Any]] = None,
    ) -> Optional[Tuple[bool, Optional[str]]]:
        """Send a Discord message via Gateway or REST if configured; else None."""
        token = (self.db.get_setting("discord_token", "") or "").strip()
        channel_id = (self.db.get_setting("discord_channel_id", "") or "").strip()
        if not token or not channel_id:
            return None

        if (
            self.discord_manager is not None
            and self.discord_manager.is_connected()
            and self.discord_manager.client is not None
            and self.discord_manager._active_token == token
        ):
            try:
                import discord as discord_lib

                channel = self.discord_manager.client.get_channel(int(channel_id))
                if channel is not None:
                    if embed is not None:
                        await channel.send(
                            message, embed=discord_lib.Embed.from_dict(embed)
                        )
                    else:
                        await channel.send(message)
                    return True, None
            except Exception as exc:
                logger.warning(
                    "Gateway send failed, falling back to Discord REST API: %s", exc
                )

        if embed is not None:
            return await send_discord_rest_message(
                token, channel_id, message, embed=embed
            )
        return await send_discord_rest_message(token, channel_id, message)

    async def _dispatch_multi_channel(
        self,
        *,
        discord_message: str,
        discord_embed: Optional[dict[str, Any]],
        telegram_message: str,
        webhook_payload: dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """Dispatch notifications to all enabled and configured channels."""
        any_sent = False
        errors: list[str] = []

        discord_res = await self._send_discord_alert(
            discord_message, embed=discord_embed
        )
        if discord_res is not None:
            ok, err = discord_res
            if ok:
                any_sent = True
            elif err:
                errors.append(f"Discord: {err}")

        if self.telegram_enabled():
            tg_token = (self.db.get_setting("telegram_bot_token", "") or "").strip()
            tg_chat = (self.db.get_setting("telegram_chat_id", "") or "").strip()
            if tg_token and tg_chat:
                ok, err = await send_telegram_notification(
                    tg_token, tg_chat, telegram_message
                )
                if ok:
                    any_sent = True
                elif err:
                    errors.append(f"Telegram: {err}")

        if self.webhook_enabled():
            webhook_url = (self.db.get_setting("webhook_url", "") or "").strip()
            if webhook_url:
                ok, err = await send_webhook_notification(webhook_url, webhook_payload)
                if ok:
                    any_sent = True
                elif err:
                    errors.append(f"Webhook: {err}")

        return any_sent, ("; ".join(errors) if errors else None)

    async def _send_in_stock_alert(
        self,
        product: dict[str, Any],
        price: Optional[float] = None,
        currency: str = "USD",
    ) -> Tuple[bool, Optional[str]]:
        """Send an in-stock alert across enabled notification channels."""
        if not self.notifications_enabled():
            return False, None

        detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        price_line = (
            f"\n**Price:** {format_price(price, currency)}" if price is not None else ""
        )
        discord_message = (
            f"🚨 **{product['name']} is back in stock!** 🚨\n"
            f"**Link:** {product['url']}{price_line}\n"
            f"**Detected:** {detected_at}"
        )
        tg_price_line = (
            f"\nPrice: {format_price(price, currency)}" if price is not None else ""
        )
        telegram_message = (
            f"🚨 {product['name']} is back in stock!\n"
            f"Link: {product['url']}{tg_price_line}\n"
            f"Detected: {detected_at}"
        )
        webhook_payload = build_webhook_payload(
            event="stock_alert",
            product=product["name"],
            url=product["url"],
            status="in_stock",
            price=price,
            message=f"{product['name']} is back in stock!",
        )

        return await self._dispatch_multi_channel(
            discord_message=discord_message,
            discord_embed=None,
            telegram_message=telegram_message,
            webhook_payload=webhook_payload,
        )

    async def _send_price_drop_alert(
        self,
        product: dict[str, Any],
        old_price: float,
        new_price: float,
        currency: str = "USD",
        is_in_stock: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Send a price drop alert across enabled notification channels."""
        if not (self.notifications_enabled() and self.price_alerts_enabled()):
            return False, None

        old_fmt = format_price(old_price, currency)
        new_fmt = format_price(new_price, currency)
        drop_amt = old_price - new_price
        pct = (drop_amt / old_price * 100.0) if old_price > 0 else 0.0
        summary = f"{product['name']} dropped from {old_fmt} to {new_fmt} (-{pct:.1f}%)"

        discord_message = f"💰 **Price Drop Alert: {product['name']}**"
        discord_embed = {
            "title": f"💰 Price Drop: {product['name']}",
            "url": product["url"],
            "description": (
                f"Price dropped by **{format_price(drop_amt, currency)}** "
                f"({pct:.1f}% off)!"
            ),
            "color": 0x22C55E,
            "fields": [
                {"name": "Previous Price", "value": old_fmt, "inline": True},
                {"name": "New Price", "value": new_fmt, "inline": True},
                {
                    "name": "Stock Status",
                    "value": "In Stock" if is_in_stock else "Out of Stock",
                    "inline": True,
                },
            ],
            "timestamp": utc_now_iso(),
        }
        telegram_message = (
            f"💰 Price Drop Alert: {product['name']}\n"
            f"Previous Price: {old_fmt}\n"
            f"New Price: {new_fmt} (-{pct:.1f}%)\n"
            f"Link: {product['url']}"
        )
        webhook_payload = build_webhook_payload(
            event="price_drop",
            product=product["name"],
            url=product["url"],
            status="in_stock" if is_in_stock else "out_of_stock",
            price=new_price,
            message=summary,
        )

        return await self._dispatch_multi_channel(
            discord_message=discord_message,
            discord_embed=discord_embed,
            telegram_message=telegram_message,
            webhook_payload=webhook_payload,
        )

    async def _check_and_record(
        self, product: dict[str, Any], record_unchanged: bool = False
    ) -> dict[str, Any]:
        """Check one product, update SQLite state, record price, and notify."""
        was_in_stock = bool(product["in_stock"])
        previous_price: Optional[float] = product.get("current_price")

        delay = self.get_domain_rate_limit_delay()
        await self._rate_limiter.wait_if_needed(product["url"], delay)
        proxy = self.get_next_proxy()

        if proxy is not None:
            result = await StockChecker.check_product(product, proxy=proxy)
        else:
            result = await StockChecker.check_product(product)

        is_in_stock = bool(result["in_stock"])
        error = result["error"]
        strategy_used = result["strategy_used"]
        detected_by = result["detected_by"]
        details = result["details"]
        detected_price: Optional[float] = result.get("price")
        detected_currency: str = result.get("currency") or "USD"
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

        if detected_price is not None:
            self.db.record_price(
                product_id=product["id"],
                product_name=product["name"],
                price=detected_price,
                currency=detected_currency,
            )

        status_changed = is_in_stock != was_in_stock
        updated = self.db.update_product_check_result(
            product_id=product["id"],
            in_stock=is_in_stock,
            error=None,
            checked_at=checked_at,
            status_changed=status_changed,
            strategy_details=strategy_details,
            current_price=detected_price,
            currency=detected_currency if detected_price is not None else None,
        )

        if is_in_stock and not was_in_stock:
            sent, notify_err = await self._send_in_stock_alert(
                product, price=detected_price, currency=detected_currency
            )
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

        if (
            detected_price is not None
            and previous_price is not None
            and detected_price < previous_price
        ):
            price_sent, price_err = await self._send_price_drop_alert(
                product,
                old_price=previous_price,
                new_price=detected_price,
                currency=detected_currency,
                is_in_stock=is_in_stock,
            )
            old_fmt = format_price(previous_price, detected_currency)
            new_fmt = format_price(detected_price, detected_currency)
            drop_msg = f"Price drop for {product['name']}: {old_fmt} -> {new_fmt}"
            if price_err:
                drop_msg = f"{drop_msg} (Notification failed: {price_err})"
                logger.error(
                    "Price drop notification error for %s: %s",
                    product["name"],
                    price_err,
                )
            self.db.add_history(
                product_id=product["id"],
                product_name=product["name"],
                status="price_drop",
                notification_sent=price_sent,
                message=drop_msg,
            )

        return updated or product

    async def _check_product(
        self, product: dict[str, Any], record_unchanged: bool = False
    ) -> dict[str, Any]:
        """Alias for _check_and_record()."""
        return await self._check_and_record(product, record_unchanged=record_unchanged)

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
