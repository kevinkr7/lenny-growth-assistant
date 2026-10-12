from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


def rooted_path(value: str | Path) -> Path:
    """Resolve a configurable project file path without depending on the CWD."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://lenny:lenny_local_only@localhost:5433/lenny"
    default_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    pi_agent_url: str = "http://localhost:8001"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_timeout_seconds: int = Field(90, ge=5, le=300)
    model_timeout_seconds: int = Field(360, ge=5, le=600)
    model_max_tokens: int = Field(3500, ge=256, le=8000)
    ollama_context_tokens: int = Field(8192, ge=8192, le=32768)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = str(ROOT / "data" / "embeddings")
    rag_top_k: int = Field(8, ge=1, le=12)
    rag_min_score: float = Field(0.45, ge=0, le=1)
    context_messages: int = Field(8, ge=1, le=20)
    context_char_limit: int = Field(8000, ge=1000, le=16000)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    transcript_repository: str = "https://github.com/ChatPRD/lennys-podcast-transcripts"
    transcript_revision: str = "main"
    demo_sources_file: str = "evals/demo-sources.json"
    ship30_guide_file: str = "skills/growth-essay/ESSAY_GUIDE.md"
    database_pool_size: int = Field(8, ge=1, le=30)


@lru_cache
def settings() -> Settings:
    return Settings()
