"""Log management utilities for UB Stock Bot."""

import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path

from config import LOG_DIR, LOG_FILE


class LogManager:
    """Manages logging configuration and cleanup."""
    
    @staticmethod
    def setup_logging() -> None:
        """Configure logging with file rotation and console output."""
        # Create logs directory if it doesn't exist
        if not os.path.exists(LOG_DIR):
            os.makedirs(LOG_DIR)

        # Configure logging with RotatingFileHandler
        log_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # File handler for main log file, rotates after 5MB, keeps 5 backups
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,  # Keep 5 backup files
            encoding="utf-8",
        )
        file_handler.setFormatter(log_formatter)

        # Stream handler for console output
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(log_formatter)

        # Get root logger and add handlers
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)

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
                # Check if it's a file, ends with .log, and is not the current main log
                if (
                    file_path.is_file()
                    and file_path.suffix == ".log"
                    and file_path.name != current_log_file_name
                ):
                    # Check if the file was modified more than a week ago (7 days)
                    seven_days_ago = datetime.now().timestamp() - (7 * 24 * 60 * 60)
                    if file_path.stat().st_mtime < seven_days_ago:
                        os.remove(file_path)
                        logging.info(f"Deleted old log file: {file_path}")
        except Exception as e:
            logging.error(f"Error cleaning logs: {e}")
