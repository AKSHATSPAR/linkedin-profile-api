from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def dash_profile() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "dash_profile.json"
    return json.loads(path.read_text())
