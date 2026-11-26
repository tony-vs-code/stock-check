"""HTTP and retry utilities for UB Stock Bot."""

import asyncio
import logging
from typing import Any, Callable

import aiohttp

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def retry(retries: int = 3, delay: float = 1.0) -> Callable:
    """Retry an async function on aiohttp.ClientError or asyncio.TimeoutError."""

    def decorator(func: Callable) -> Callable:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = exc
                    logging.error("Attempt %d failed: %s", attempt + 1, exc)
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
            raise Exception(f"All {retries} attempts failed: {last_error}")

        return wrapper

    return decorator


@retry(retries=2, delay=1.0)
async def fetch_product_page(url: str) -> str:
    """Fetch HTML content for a product page."""
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(
        timeout=timeout, headers=DEFAULT_HEADERS
    ) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise aiohttp.ClientError(
                    f"Failed to fetch {url}: Status code {response.status}"
                )
            return await response.text()
