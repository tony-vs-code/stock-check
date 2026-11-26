"""Log management utilities for UB Stock Bot."""

import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path

from config import LOG_DIR, LOG_FILE


class LogManager:
    """Manages logging configuration and cleanup."""

    _configured: bool = False

    @classmethod
    def setup_logging(cls) -> None:
        """Configure logging with file rotation and console output."""
        if cls._configured:
            return

        os.makedirs(LOG_DIR, exist_ok=True)

        log_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(log_formatter)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(log_formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)
        cls._configured = True

    @staticmethod
    def clean_old_logs() -> None:
        """Clean up old log files from the logs directory."""
        logging.debug("Cleaning old log files")
        try:
            log_dir = Path(LOG_DIR)
            if not log_dir.exists():
                log_dir.mkdir(parents=True, exist_ok=True)
                return

            current_log_file_name = Path(LOG_FILE).name

            for file_path in log_dir.iterdir():
                if (
                    file_path.is_file()
                    and file_path.suffix == ".log"
                    and file_path.name != current_log_file_name
                ):
                    seven_days_ago = datetime.now().timestamp() - (7 * 24 * 60 * 60)
                    if file_path.stat().st_mtime < seven_days_ago:
                        os.remove(file_path)
                        logging.info("Deleted old log file: %s", file_path)
        except Exception as e:
            logging.error("Error cleaning logs: %s", e)
