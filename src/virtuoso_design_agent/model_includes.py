"""Resolve validated Spectre model include bundles from a PDK profile."""

from __future__ import annotations

import re
from collections.abc import Mapping


_SECTION_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def resolve_spectre_model_includes(
    profile: Mapping[str, object],
    process_corner: str | None = None,
) -> list[dict[str, str]]:
    """Return the ordered model bundle for a corner, with legacy fallback."""

    default_section = str(profile.get("model_section") or "")
    selected_corner = default_section if process_corner is None else process_corner
    if _SECTION_PATTERN.fullmatch(selected_corner) is None:
        raise ValueError(f"invalid Spectre process corner: {selected_corner!r}")

    raw_includes: object | None = None
    raw_corners = profile.get("process_corners", {})
    if isinstance(raw_corners, Mapping):
        raw_includes = raw_corners.get(selected_corner)
    if raw_includes is None:
        if selected_corner != default_section:
            raise ValueError(
                f"PDK profile does not map process corner {selected_corner!r}"
            )
        raw_includes = [
            {
                "path": str(profile.get("model_include") or ""),
                "section": default_section,
            }
        ]
    if not isinstance(raw_includes, list) or not raw_includes:
        raise ValueError(
            f"PDK profile has no model includes for process corner "
            f"{selected_corner!r}"
        )

    includes: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for item in raw_includes:
        if not isinstance(item, Mapping):
            raise ValueError(
                f"invalid model include for process corner {selected_corner!r}"
            )
        path = str(item.get("path") or "")
        section = str(item.get("section") or "")
        if (
            not path
            or '"' in path
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or _SECTION_PATTERN.fullmatch(section) is None
        ):
            raise ValueError(
                f"invalid model include for process corner {selected_corner!r}"
            )
        identity = (path, section)
        if identity in identities:
            raise ValueError(
                f"duplicate model include for process corner {selected_corner!r}"
            )
        identities.add(identity)
        includes.append({"path": path, "section": section})
    return includes


def render_spectre_model_includes(
    profile: Mapping[str, object],
    process_corner: str | None = None,
) -> str:
    """Render the resolved model bundle in deterministic profile order."""

    return "\n".join(
        f'include "{item["path"]}" section={item["section"]}'
        for item in resolve_spectre_model_includes(profile, process_corner)
    )
