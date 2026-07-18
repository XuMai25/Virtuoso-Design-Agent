"""Execution adapters."""

from .base import AdapterResult, DesignAdapter
from .demo import DeterministicDemoAdapter
from .subprocess_bridge import SubprocessBridgeAdapter

__all__ = [
    "AdapterResult",
    "DesignAdapter",
    "DeterministicDemoAdapter",
    "SubprocessBridgeAdapter",
]
