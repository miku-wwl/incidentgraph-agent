from __future__ import annotations

from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import (
    DatabaseUnavailable,
    GraphQueryError,
    IncompleteGraphEvidence,
)


class _Connection:
    def __init__(self, *responses):
        self.responses = iter(responses)

    def execute_command(self, *args):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def _store(*responses, graph=None) -> FalkorStore:
    store = FalkorStore.__new__(FalkorStore)
    store.client = SimpleNamespace(connection=_Connection(*responses))
    store.graph = graph
    store.settings = SimpleNamespace(query_timeout_ms=5000)
    return store


@pytest.mark.parametrize(
    "response",
    [
        ["RESULTSET_SIZE", 501],
        ["resultset_size", "1200"],
        ["RESULTSET_SIZE", -1],
    ],
)
def test_resultset_safety_accepts_sentinel_capacity_and_unlimited(response) -> None:
    _store(response)._verify_resultset_size()


@pytest.mark.parametrize("limit", [0, 1, 500])
def test_resultset_safety_rejects_limit_below_sentinel(limit: int) -> None:
    with pytest.raises(IncompleteGraphEvidence):
        _store(["RESULTSET_SIZE", limit])._verify_resultset_size()


@pytest.mark.parametrize(
    "response",
    [None, ["OTHER_SETTING", 10000], ["RESULTSET_SIZE", "unlimited"]],
)
def test_resultset_safety_rejects_malformed_configuration(response) -> None:
    with pytest.raises(GraphQueryError):
        _store(response)._verify_resultset_size()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ResponseError("configuration unavailable"), GraphQueryError),
        (RedisConnectionError("connection unavailable"), DatabaseUnavailable),
    ],
)
def test_resultset_safety_fails_closed_when_configuration_read_fails(
    error: Exception, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        _store(error)._verify_resultset_size()


def test_runtime_lowering_between_pre_and_post_check_discards_query_result() -> None:
    graph = SimpleNamespace(
        ro_query=lambda query, params=None, timeout=None: SimpleNamespace(
            result_set=[["apparently complete"]]
        )
    )
    store = _store(["RESULTSET_SIZE", 501], ["RESULTSET_SIZE", 500], graph=graph)

    with pytest.raises(IncompleteGraphEvidence):
        store.read("RETURN 'apparently complete'", {})


def test_lowered_runtime_limit_blocks_the_next_query_before_execution() -> None:
    graph = SimpleNamespace(ro_query=lambda *args, **kwargs: pytest.fail("query ran"))
    store = _store(["RESULTSET_SIZE", 500], graph=graph)

    with pytest.raises(IncompleteGraphEvidence):
        store.read("RETURN 1", {})
