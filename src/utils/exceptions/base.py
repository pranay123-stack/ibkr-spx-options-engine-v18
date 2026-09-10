"""
Base Exception Classes for client Trading Engine

Author: client Options Trading Engine
"""

from typing import Optional, Dict, Any


class clientError(Exception):
    """Base exception for all trading engine errors"""

    def __init__(
        self,
        code: int,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ):
        self.code = code
        self.message = message
        self.context = context or {}
        super().__init__(f"[E{code}] {message}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "context": self.context,
            "type": self.__class__.__name__
        }
