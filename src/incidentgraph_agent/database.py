from __future__ import annotations

from typing import Any

from falkordb import FalkorDB
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError, TimeoutError as RedisTimeoutError

from incidentgraph_agent.config import Settings
from incidentgraph_agent.errors import (
    DatabaseUnavailable,
    GraphQueryError,
    IncompleteGraphEvidence,
)

_MINIMUM_RESULTSET_SIZE = 501


class FalkorStore:
    """Small connection wrapper; graph reads use FalkorDB's read-only command."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        try:
            self.client = FalkorDB(
                host=settings.host,
                port=settings.port,
                username="default",
                password=settings.password,
                socket_connect_timeout=2,
                socket_timeout=8,
                client_name="incidentgraph-agent",
            )
            self.graph = self.client.select_graph(settings.graph_name)
            self.client.connection.ping()
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            client = getattr(self, "client", None)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            raise DatabaseUnavailable(
                "Cannot connect to the configured local FalkorDB instance."
            ) from exc

    def verify(self) -> dict[str, Any]:
        try:
            module_rows = self.client.connection.execute_command("MODULE", "LIST")
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            raise DatabaseUnavailable(
                "Cannot query the configured local FalkorDB instance."
            ) from exc
        except ResponseError as exc:
            raise GraphQueryError("Could not inspect loaded database modules.") from exc

        graph_module: dict[str, Any] | None = None
        for row in module_rows:
            values = list(row)
            module = {
                str(values[index]): values[index + 1]
                for index in range(0, len(values), 2)
            }
            if module.get("name") == "graph":
                graph_module = module
                break
        if graph_module is None:
            raise GraphQueryError("The FalkorDB graph module is not loaded.")

        try:
            command_info = self.client.connection.execute_command(
                "COMMAND", "INFO", "GRAPH.QUERY"
            )
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            raise DatabaseUnavailable(
                "Cannot inspect graph commands on the configured database."
            ) from exc
        except ResponseError as exc:
            raise GraphQueryError(
                "Could not inspect the FalkorDB query command."
            ) from exc
        if not command_info.get("graph.QUERY"):
            raise GraphQueryError("The FalkorDB GRAPH.QUERY command is unavailable.")
        return {
            "redis_ping": "PONG",
            "graph_module": "loaded",
            "graph_module_version": graph_module.get("ver"),
            "graph_query_command": "available",
            "graph_name": self.settings.graph_name,
        }

    def read(self, query: str, params: dict[str, Any] | None = None) -> Any:
        # GRAPH.CONFIG values can change at runtime. Check both immediately before
        # and after the query so a newly restrictive cap cannot pass as complete.
        self._verify_resultset_size()
        result = self._execute(query, params, read_only=True)
        self._verify_resultset_size()
        return result

    def _verify_resultset_size(self) -> None:
        try:
            response = self.client.connection.execute_command(
                "GRAPH.CONFIG", "GET", "RESULTSET_SIZE"
            )
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            raise DatabaseUnavailable(
                "Cannot verify the FalkorDB result-set safety limit."
            ) from exc
        except ResponseError as exc:
            raise GraphQueryError(
                "Could not verify the FalkorDB result-set safety limit."
            ) from exc

        limit = self._parse_resultset_size(response)
        if limit is None:
            raise GraphQueryError(
                "FalkorDB returned an invalid result-set safety limit."
            )
        if 0 <= limit < _MINIMUM_RESULTSET_SIZE:
            raise IncompleteGraphEvidence(
                "FalkorDB RESULTSET_SIZE is below the 501-row evidence sentinel."
            )

    @staticmethod
    def _parse_resultset_size(response: Any) -> int | None:
        if not isinstance(response, (list, tuple)) or len(response) != 2:
            return None
        key = response[0]
        if isinstance(key, bytes):
            try:
                key = key.decode("ascii")
            except UnicodeDecodeError:
                return None
        if not isinstance(key, str) or key.upper() != "RESULTSET_SIZE":
            return None

        value = response[1]
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                return None
        if type(value) is int:
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized or normalized in {"+", "-"}:
            return None
        digits = normalized[1:] if normalized[0] in "+-" else normalized
        if not digits.isascii() or not digits.isdigit():
            return None
        try:
            return int(normalized)
        except ValueError:
            return None

    def write(self, query: str, params: dict[str, Any] | None = None) -> Any:
        return self._execute(query, params, read_only=False)

    def _execute(
        self, query: str, params: dict[str, Any] | None, *, read_only: bool
    ) -> Any:
        try:
            method = self.graph.ro_query if read_only else self.graph.query
            return method(query, params=params, timeout=self.settings.query_timeout_ms)
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            raise DatabaseUnavailable(
                "The configured FalkorDB connection became unavailable."
            ) from exc
        except ResponseError as exc:
            raise GraphQueryError(
                "FalkorDB rejected a predefined application query."
            ) from exc

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> FalkorStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
