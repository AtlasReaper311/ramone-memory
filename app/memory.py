"""ChromaDB persistence for long-term memories.

One collection (ramone_memory), cosine space, one document per finished
session. The document text is the distilled summary; everything else
rides in metadata. Chroma metadata values must be scalar, so tags are
stored comma-joined and raw turns as a JSON string, both rehydrated on
read.
"""

import json
import logging
import time
import uuid

import chromadb
from chromadb.api.models.Collection import Collection

from app.config import Settings
from app.models import MemoryOut, MemoryRef, Turn

logger = logging.getLogger(__name__)

READINESS_ATTEMPTS = 30
READINESS_DELAY_SECONDS = 2.0


def _to_memory_out(doc_id: str, document: str, meta: dict) -> MemoryOut:
    """Rehydrate one Chroma record into the typed API shape."""
    raw: list[Turn] = []
    try:
        raw = [Turn(**t) for t in json.loads(meta.get("raw_turns", "[]"))]
    except (json.JSONDecodeError, TypeError, ValueError):
        # A memory with unreadable turns is still a memory; the summary
        # is the payload that matters. Degrade, never drop.
        logger.warning("memory %s has unreadable raw_turns; returning []", doc_id)
    ts = float(meta.get("timestamp", 0.0))
    tags = [t for t in str(meta.get("tags", "")).split(",") if t]
    return MemoryOut(
        id=doc_id,
        session_id=str(meta.get("session_id", "")),
        timestamp=ts,
        timestamp_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        summary=document,
        tags=tags,
        raw_turns=raw,
    )


class MemoryStore:
    """All reads and writes against the ramone_memory collection."""

    def __init__(self, settings: Settings) -> None:
        """Connect to Chroma with retries and open the collection.

        Containers race their dependencies on boot; retrying here means
        `docker compose up` succeeds regardless of whether Chroma or
        this service wins the race. Cosine space is set at creation
        time because it cannot be changed after the collection exists.
        """
        last_error: Exception | None = None
        for attempt in range(1, READINESS_ATTEMPTS + 1):
            try:
                client = chromadb.HttpClient(
                    host=settings.chroma_host, port=settings.chroma_port
                )
                client.heartbeat()
                self.collection: Collection = client.get_or_create_collection(
                    name=settings.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(
                    "Chroma ready at %s:%d, collection %s (%d memories)",
                    settings.chroma_host,
                    settings.chroma_port,
                    settings.collection_name,
                    self.collection.count(),
                )
                return
            except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
                last_error = exc
                logger.info(
                    "Waiting for Chroma at %s:%d (attempt %d/%d)",
                    settings.chroma_host,
                    settings.chroma_port,
                    attempt,
                    READINESS_ATTEMPTS,
                )
                time.sleep(READINESS_DELAY_SECONDS)
        raise RuntimeError(f"Chroma unreachable: {last_error}")

    def heartbeat(self) -> bool:
        """Report whether the collection still answers."""
        try:
            self.collection.count()
            return True
        except Exception:  # noqa: BLE001
            return False

    def count(self) -> int:
        """Number of stored memories."""
        return self.collection.count()

    def add_memory(
        self,
        session_id: str,
        summary: str,
        tags: list[str],
        raw_turns: list[Turn],
        embedding: list[float],
    ) -> str:
        """Persist one finished session and return its memory id."""
        memory_id = uuid.uuid4().hex
        self.collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            documents=[summary],
            metadatas=[
                {
                    "session_id": session_id,
                    "timestamp": time.time(),
                    "tags": ",".join(tags),
                    "raw_turns": json.dumps(
                        [t.model_dump() for t in raw_turns], ensure_ascii=False
                    ),
                }
            ],
        )
        return memory_id

    def query(self, embedding: list[float], k: int) -> list[MemoryRef]:
        """Return the k most semantically relevant memories, best first.

        Chroma raises when n_results exceeds the collection size, so the
        ask is clamped rather than letting an empty store 500 a chat.
        """
        total = self.collection.count()
        if total == 0:
            return []
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(k, total),
            include=["metadatas", "documents", "distances"],
        )
        refs: list[MemoryRef] = []
        for doc_id, document, meta, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            refs.append(
                MemoryRef(
                    id=doc_id,
                    timestamp=float(meta.get("timestamp", 0.0)),
                    summary=document,
                    tags=[t for t in str(meta.get("tags", "")).split(",") if t],
                    # Cosine distance to similarity: normalised embeddings
                    # make 1 - distance a readable 0..1 score.
                    score=round(1.0 - float(distance), 4),
                )
            )
        return refs

    def list_memories(
        self,
        page: int,
        page_size: int,
        start_ts: float | None,
        end_ts: float | None,
    ) -> tuple[list[MemoryOut], int]:
        """Return one page of memories (newest first) and the filtered total.

        Chroma's get() has no ordering, so the filtered set is pulled and
        sorted here. At personal-assistant scale (hundreds of sessions a
        year) that is a trivially small in-memory sort; revisit only if
        the collection ever reaches tens of thousands.
        """
        clauses: list[dict] = []
        if start_ts is not None:
            clauses.append({"timestamp": {"$gte": start_ts}})
        if end_ts is not None:
            clauses.append({"timestamp": {"$lte": end_ts}})
        where: dict | None = None
        if len(clauses) == 1:
            where = clauses[0]
        elif len(clauses) > 1:
            where = {"$and": clauses}

        result = self.collection.get(where=where, include=["metadatas", "documents"])
        rows = [
            _to_memory_out(doc_id, document, meta)
            for doc_id, document, meta in zip(
                result["ids"], result["documents"], result["metadatas"]
            )
        ]
        rows.sort(key=lambda m: m.timestamp, reverse=True)
        total = len(rows)
        offset = (page - 1) * page_size
        return rows[offset : offset + page_size], total

    def delete(self, memory_id: str) -> bool:
        """Delete one memory by id; False when the id does not exist."""
        existing = self.collection.get(ids=[memory_id])
        if not existing["ids"]:
            return False
        self.collection.delete(ids=[memory_id])
        return True
