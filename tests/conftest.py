"""
Shared pytest fixtures and import setup.

The application code is a flat collection of top-level modules (neo4j_ops.py,
ai_compliance.py, sepsis_compliance.py, ...) rather than an installable
package, so this conftest adds the project root to sys.path and forces
USE_GRAPH_DEMO off before any application module is imported, ensuring tests
exercise the real (mocked) Neo4j code paths rather than the demo-data
fallback branches.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os

os.environ["USE_GRAPH_DEMO"] = ""

import pytest


@pytest.fixture(autouse=True)
def _no_graph_demo_mode(monkeypatch):
    """Ensure USE_GRAPH_DEMO is off by default for every test unless overridden."""
    monkeypatch.setenv("USE_GRAPH_DEMO", "")
    monkeypatch.setattr("neo4j_config.USE_GRAPH_DEMO", False, raising=False)
