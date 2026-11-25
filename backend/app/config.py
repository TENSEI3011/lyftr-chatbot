"""All configuration comes from environment variables."""
import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    llm_api_keys: list[str] = field(default_factory=lambda: _list("LLM_API_KEYS"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./app.db"))
    allowed_origins: list[str] = field(
        default_factory=lambda: _list("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
    )
    enable_failure_simulation: bool = field(default_factory=lambda: _bool("ENABLE_FAILURE_SIMULATION"))
    default_system_prompt: str = field(
        default_factory=lambda: os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant.")
    )
    cache_type: str = field(default_factory=lambda: os.getenv("CACHE_TYPE", "local"))  # local | disk
    cache_dir: str = field(default_factory=lambda: os.getenv("CACHE_DIR", "/data/llm_cache"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "30")))
    # Local/dev only: answer with a deterministic fake reply instead of calling a provider.
    mock_llm: bool = field(default_factory=lambda: _bool("MOCK_LLM"))

    @property
    def sqlite_path(self) -> str:
        url = self.database_url
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if url.startswith(prefix):
                return url[len(prefix):] or ":memory:"
        return url
