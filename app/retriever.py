"""Semantic memory retrieval and system-prompt injection.

The retrieval path is deliberately three small, separately testable
steps: embed the incoming text, query the store, render the hits into
a bounded text block. Injection then merges that block into whatever
system message the caller already sent, so the proxy adds context
without ever replacing the caller's own instructions.
"""

import logging
import time

import httpx

from app.config import Settings
from app.memory import MemoryStore
from app.models import MemoryRef

logger = logging.getLogger(__name__)

_INJECTION_HEADER = (
    "Long-term memory. The following are distilled summaries of past "
    "conversations with this user, retrieved because they are relevant "
    "to the current message. Treat them as background context; if the "
    "user contradicts a memory, the user is right."
)


async def embed_text(
    client: httpx.AsyncClient, settings: Settings, text: str
) -> list[float]:
    """Embed one string with the configured Ollama embedding model.

    Uses /api/embed (the batched endpoint) with a single input so the
    same call shape works if batching is ever needed here.
    """
    response = await client.post(
        f"{settings.ollama_host}/api/embed",
        json={"model": settings.embed_model, "input": [text]},
        timeout=settings.embed_timeout_seconds,
    )
    response.raise_for_status()
    embeddings = response.json().get("embeddings", [])
    if not embeddings:
        raise RuntimeError(
            f"Ollama returned no embedding for model {settings.embed_model}; "
            "is the model pulled? (ollama pull nomic-embed-text)"
        )
    return embeddings[0]


async def retrieve_memories(
    client: httpx.AsyncClient,
    settings: Settings,
    store: MemoryStore,
    query_text: str,
) -> list[MemoryRef]:
    """Return the top-k memories relevant to query_text, best first.

    Retrieval failures degrade to an empty list rather than failing the
    conversation: a chat that arrives without memory context is worth
    more than a chat that never arrives.
    """
    try:
        embedding = await embed_text(client, settings, query_text)
        return store.query(embedding, settings.top_k)
    except Exception:  # noqa: BLE001
        logger.exception("memory retrieval failed; continuing without context")
        return []


def build_memory_block(memories: list[MemoryRef], max_chars: int) -> str:
    """Render retrieved memories into a bounded plain-text block.

    The cap protects the conversation's context window: memories inform
    the reply, they must never crowd out the message being replied to.
    """
    if not memories:
        return ""
    lines = [_INJECTION_HEADER, ""]
    for memory in memories:
        day = time.strftime("%Y-%m-%d", time.gmtime(memory.timestamp))
        suffix = f" (tags: {', '.join(memory.tags)})" if memory.tags else ""
        lines.append(f"- [{day}] {memory.summary}{suffix}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 1].rstrip() + "\u2026"
    return block


def inject_memory(
    messages: list[dict], block: str, base_system_prompt: str
) -> list[dict]:
    """Merge the memory block into the message list's system prompt.

    If the caller sent a system message, the block is appended to it so
    the caller's instructions keep priority. If not, a system message is
    created from the configured base prompt (possibly empty) plus the
    block. Returns a new list; the caller's payload is never mutated.
    """
    if not block:
        return messages
    out = [dict(m) for m in messages]
    for message in out:
        if message.get("role") == "system":
            message["content"] = f"{message.get('content', '')}\n\n{block}"
            return out
    prefix = f"{base_system_prompt}\n\n" if base_system_prompt else ""
    out.insert(0, {"role": "system", "content": f"{prefix}{block}"})
    return out
