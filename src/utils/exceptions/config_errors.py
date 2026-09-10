"""
Configuration Errors (6000-6999)

Author: client Options Trading Engine
"""

from typing import Dict
from .base import clientError


class ConfigError(clientError):
    """Base class for configuration errors"""
    pass


class ConfigFileNotFoundError(ConfigError):
    """Configuration file not found"""
    def __init__(self, file_path: str, context: Dict = None):
        ctx = {"file_path": file_path, **(context or {})}
        super().__init__(6001, f"Config file not found: {file_path}", ctx)


class ConfigParseError(ConfigError):
    """Failed to parse configuration"""
    def __init__(self, file_path: str, parse_error: str, context: Dict = None):
        ctx = {"file_path": file_path, "parse_error": parse_error, **(context or {})}
        super().__init__(6002, f"Config parse error in {file_path}: {parse_error}", ctx)


class ConfigValidationError(ConfigError):
    """Configuration validation failed"""
    def __init__(self, field: str, issue: str, context: Dict = None):
        ctx = {"field": field, "issue": issue, **(context or {})}
        super().__init__(6003, f"Config validation failed for '{field}': {issue}", ctx)


class MissingConfigError(ConfigError):
    """Required configuration missing"""
    def __init__(self, field: str, context: Dict = None):
        ctx = {"field": field, **(context or {})}
        super().__init__(6004, f"Missing required config: {field}", ctx)
