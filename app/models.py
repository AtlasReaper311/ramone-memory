"""Pydantic models for every request and response shape.

The Ollama-compatible /api/chat route deliberately has no model here:
it passes the upstream body through untouched so Home Assistant's
Ollama integration never notices the proxy. Everything this service
owns natively is typed below.
"""

from pydantic import BaseModel, Field


class Turn(BaseModel):
    """One conversational turn, as stored inside a memory document."""

    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str


class ConversationRequest(BaseModel):
    """A single turn against the explicit-session endpoint."""

    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    model: str | None = None


class MemoryRef(BaseModel):
    """A retrieved memory as injected into a conversation."""

    id: str
    timestamp: float
    summary: str
    tags: list[str]
    score: float


class ConversationResponse(BaseModel):
    """The assistant reply plus the memories that informed it."""

    session_id: str
    reply: str
    model: str
    memories_used: list[MemoryRef]


class MemoryOut(BaseModel):
    """A stored memory, as returned by GET /memories."""

    id: str
    session_id: str
    timestamp: float
    timestamp_iso: str
    summary: str
    tags: list[str]
    raw_turns: list[Turn]


class MemoryPage(BaseModel):
    """A page of stored memories, newest first."""

    items: list[MemoryOut]
    page: int
    page_size: int
    total: int


class DeleteResponse(BaseModel):
    """Confirmation that a memory was removed."""

    id: str
    deleted: bool


class EndSessionResponse(BaseModel):
    """Outcome of finalising a session into long-term memory."""

    session_id: str
    stored: bool
    memory_id: str | None = None
    reason: str


class HealthResponse(BaseModel):
    """Service health plus dependency reachability."""

    ok: bool
    service: str
    version: str
    chroma_ok: bool
    ollama_ok: bool
    memories: int
    active_sessions: int
