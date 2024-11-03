import logging
import asyncio
import aiohttp
from typing import Callable, Any


def retry(retries: int = 3, delay: int = 5) -> Callable:
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs) -> Any:
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except aiohttp.ClientError as e:
                    logging.error(f"Attempt {attempt + 1} failed: {e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
            raise Exception(f"All {retries} attempts failed.")

        return wrapper

    return decorator


@retry()
async def fetch_product_page(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise aiohttp.ClientError(f"Failed to fetch {url}: Status code {response.status}")
            return await response.text()
