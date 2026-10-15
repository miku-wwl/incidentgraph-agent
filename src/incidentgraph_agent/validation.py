from __future__ import annotations

import re

from incidentgraph_agent.errors import InvalidInput

_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


def validate_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidInput(
            f"{field} must be 1-64 lowercase letters, digits, dots, underscores, or hyphens."
        )
    return value


def validate_max_depth(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 4:
        raise InvalidInput("max_depth must be an integer from 1 through 4.")
    return value
