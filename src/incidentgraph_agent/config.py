from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

from incidentgraph_agent.errors import ConfigurationError

_GRAPH_NAME = re.compile(r"incidentgraph_[a-z0-9_-]{1,48}\Z")


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    password: str
    graph_name: str
    query_timeout_ms: int = 5_000

    @classmethod
    def from_env(cls) -> Settings:
        host = os.environ.get("FALKORDB_HOST", "127.0.0.1")
        try:
            port = int(os.environ.get("FALKORDB_HOST_PORT", "16379"))
        except ValueError as exc:
            raise ConfigurationError("FALKORDB_HOST_PORT must be an integer.") from exc

        settings = cls(
            host=host,
            port=port,
            password=os.environ.get("FALKORDB_PASSWORD", ""),
            graph_name=os.environ.get(
                "INCIDENTGRAPH_GRAPH_NAME", "incidentgraph_phase1"
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.host:
            raise ConfigurationError("FALKORDB_HOST must not be empty.")
        if self.host.lower() != "localhost":
            try:
                if not ipaddress.ip_address(self.host).is_loopback:
                    raise ConfigurationError("FALKORDB_HOST must be loopback-only.")
            except ValueError as exc:
                raise ConfigurationError(
                    "FALKORDB_HOST must be localhost or a loopback IP address."
                ) from exc
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ConfigurationError("FALKORDB_HOST_PORT must be between 1 and 65535.")
        if not self.password or self.password.startswith("replace-with-"):
            raise ConfigurationError(
                "Set FALKORDB_PASSWORD in the local environment or .env file."
            )
        if not _GRAPH_NAME.fullmatch(self.graph_name):
            raise ConfigurationError(
                "INCIDENTGRAPH_GRAPH_NAME must start with incidentgraph_ and use only lowercase letters, digits, underscores, or hyphens."
            )
