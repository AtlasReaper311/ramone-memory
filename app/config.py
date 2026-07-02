"""Typed configuration for the memory service.

Every tunable lives here and is overridable from the environment, which
keeps docker-compose.yml the single place where deployment values are
set. pydantic-settings validates types at startup, so a typo like
TOP_K=thr33 fails the boot loudly instead of corrupting retrieval
quietly. Defaults here are duplicated as ${VAR:-default} in
docker-compose.yml so the stack boots with no .env file at all.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "ramone-memory"
SERVICE_VERSION = "1.0.0"


class Settings(BaseSettings):
    """All runtime configuration, sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Ollama runs outside compose on the host because it owns the GPU.
    # docker-compose.yml maps host.docker.internal on native Linux too
    # via extra_hosts, making this default work on every platform.
    ollama_host: str = "http://host.docker.internal:11434"
    chat_model: str = "llama3.1:8b"
    embed_model: str = "nomic-embed-text"

    # Matches the Phase 1 voice-pipeline context budget. The injected
    # memory block is capped separately (max_injected_chars) so long
    # memories can never starve the live conversation of context.
    num_ctx: int = 4096
    # Summaries should restate the session, not improvise around it.
    temperature: float = 0.2

    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    collection_name: str = "ramone_memory"

    # Retrieval: how many past memories ride into the system prompt.
    top_k: int = 3
    max_injected_chars: int = 2400

    # Session lifecycle. Ollama's API has no "conversation over" event,
    # so a session is finalised either explicitly (POST /sessions/{id}/end)
    # or after this much idle time, checked by a background reaper.
    session_idle_seconds: int = 300
    reaper_interval_seconds: int = 30
    raw_turns_kept: int = 8

    # Optional base system prompt prepended when the caller sends none.
    # Empty by default so the proxy stays transparent for HA, which
    # supplies its own prompt from the Ollama integration config.
    system_prompt: str = ""

    # Generation is the slow call by far; embeddings are quick.
    # Health checks stay snappy.
    embed_timeout_seconds: float = 120.0
    generate_timeout_seconds: float = 300.0
    health_timeout_seconds: float = 5.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    lru_cache makes this a lazy singleton: the environment is read once,
    and every module sees the same validated object.
    """
    return Settings()
