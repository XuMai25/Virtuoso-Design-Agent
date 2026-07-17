from __future__ import annotations

from typing import Protocol


class ZoteroReader(Protocol):
    """Future read-only Zotero boundary; no SQLite or write methods belong here."""

    def status(self) -> dict[str, str | bool]: ...
