"""Utility functions and logging configuration."""

import logging
from logging.handlers import TimedRotatingFileHandler
import os

# Logging configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = 'bot.log'

# Create logger
logger = logging.getLogger('video_bot')
logger.setLevel(getattr(logging, LOG_LEVEL))

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# File handler with rotation (24h intervals, keep 2 backups = 48h max)
file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when='midnight',
    interval=1,
    backupCount=2,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


async def get_user_setting(db, user_id: int, key: str, default=None):
    """Get a user setting from database."""
    value = await db.get_user_setting(user_id, key)
    return value if value is not None else default


async def set_user_setting(db, user_id: int, key: str, value: str):
    """Set a user setting in database."""
    await db.set_user_setting(user_id, key, value)
