import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEYS", "fake-key-1,fake-key-2")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("CACHE_TYPE", "local")
    app = create_app(Settings())
    import litellm
    litellm.cache.cache.flush_cache() if hasattr(litellm.cache.cache, "flush_cache") else None
    return TestClient(app)
