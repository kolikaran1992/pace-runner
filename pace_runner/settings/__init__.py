from .config import config
from .logger import get_logger

logger = get_logger("pace_runner")


__all__ = ["config", "logger"]
