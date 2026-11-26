"""
UB Stock Monitor - Web UI, REST API, stock checker, and optional Discord bot.

Monitors product pages for stock availability and sends notifications when
products come back in stock, with SQLite persistence and a modern Web UI.
"""

import logging

import uvicorn

from api import app
from config import HOST, PORT
from log_manager import LogManager


def main() -> None:
    """Start the UB Stock Monitor web server and background services."""
    LogManager.setup_logging()
    logging.info("Starting UB Stock Monitor on http://%s:%s", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
