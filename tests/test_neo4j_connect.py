"""
Unit tests for neo4j_connect.py — the thin driver/session wrapper.

A fake driver/session pair is used to avoid opening a real Bolt connection.
"""
from __future__ import annotations

import pytest

import neo4j_connect


class FakeSession:
    def __init__(self, records):
        self._records = records
        self.last_cypher = None
        self.last_params = None

    def run(self, cypher, params):
        self.last_cypher = cypher
        self.last_params = params
        return self._records

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def __init__(self, records=None):
        self.records = records or []
        self.closed = False
        self.session_kwargs = None
        self._session = FakeSession(self.records)

    def session(self, database=None):
        self.session_kwargs = {"database": database}
        return self._session

    def close(self):
        self.closed = True

    def verify_connectivity(self):
        return True


def test_run_query_raises_when_demo_mode_enabled(monkeypatch):
    monkeypatch.setattr(neo4j_connect, "USE_GRAPH_DEMO", True)

    with pytest.raises(RuntimeError, match="USE_GRAPH_DEMO"):
        neo4j_connect.run_query("MATCH (n) RETURN n")


def test_run_query_returns_list_of_dicts_and_closes_driver(monkeypatch):
    fake_driver = FakeDriver(records=[{"id": "P1"}, {"id": "P2"}])
    monkeypatch.setattr(neo4j_connect, "USE_GRAPH_DEMO", False)
    monkeypatch.setattr(neo4j_connect, "get_driver", lambda: fake_driver)

    result = neo4j_connect.run_query("MATCH (p:Patient) RETURN p.id AS id", {"x": 1})

    assert result == [{"id": "P1"}, {"id": "P2"}]
    assert fake_driver.closed is True
    assert fake_driver._session.last_params == {"x": 1}


def test_run_query_defaults_missing_parameters_to_empty_dict(monkeypatch):
    fake_driver = FakeDriver(records=[])
    monkeypatch.setattr(neo4j_connect, "USE_GRAPH_DEMO", False)
    monkeypatch.setattr(neo4j_connect, "get_driver", lambda: fake_driver)

    neo4j_connect.run_query("MATCH (n) RETURN n")

    assert fake_driver._session.last_params == {}


def test_run_query_closes_driver_even_if_query_raises(monkeypatch):
    class RaisingSession(FakeSession):
        def run(self, cypher, params):
            raise ValueError("boom")

    fake_driver = FakeDriver()
    fake_driver._session = RaisingSession([])
    monkeypatch.setattr(neo4j_connect, "USE_GRAPH_DEMO", False)
    monkeypatch.setattr(neo4j_connect, "get_driver", lambda: fake_driver)

    with pytest.raises(ValueError):
        neo4j_connect.run_query("MATCH (n) RETURN n")

    assert fake_driver.closed is True


def test_verify_connection_short_circuits_in_demo_mode(monkeypatch):
    monkeypatch.setattr(neo4j_connect, "USE_GRAPH_DEMO", True)

    assert neo4j_connect.verify_connection() is True


def test_verify_connection_wraps_failures_in_runtime_error(monkeypatch):
    class FailingDriver(FakeDriver):
        def verify_connectivity(self):
            raise OSError("connection refused")

    monkeypatch.setattr(neo4j_connect, "USE_GRAPH_DEMO", False)
    monkeypatch.setattr(neo4j_connect, "get_driver", lambda: FailingDriver())

    with pytest.raises(RuntimeError, match="Neo4j connection failed"):
        neo4j_connect.verify_connection()


def test_get_driver_passes_configured_credentials(monkeypatch):
    captured = {}

    class FakeGraphDatabase:
        @staticmethod
        def driver(uri, auth):
            captured["uri"] = uri
            captured["auth"] = auth
            return "a-driver"

    monkeypatch.setattr(neo4j_connect, "GraphDatabase", FakeGraphDatabase)
    monkeypatch.setattr(neo4j_connect, "NEO4J_URI", "bolt://example:7687")
    monkeypatch.setattr(neo4j_connect, "NEO4J_USER", "neo4j")
    monkeypatch.setattr(neo4j_connect, "NEO4J_PASSWORD", "secret")

    driver = neo4j_connect.get_driver()

    assert driver == "a-driver"
    assert captured["uri"] == "bolt://example:7687"
    assert captured["auth"] == ("neo4j", "secret")
