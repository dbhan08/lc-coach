import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(monkeypatch) -> Path:
    """Point LC_COACH_DB at a fresh temp file for the duration of the test."""
    tmp = Path(tempfile.mkdtemp()) / "state.db"
    monkeypatch.setenv("LC_COACH_DB", str(tmp))
    return tmp
