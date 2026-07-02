"""FastAPI service: the memory layer between Home Assistant and Ollama.

Two personalities in one service. To Home Assistant it looks like
Ollama: POST /api/chat behaves identically (streaming included) except
that relevant long-term memories are injected into the system prompt
on the way through. To everything else it is a memory API: sessions
can be ended explicitly, and stored memories are listable, filterable,
and deletable.

Session lifecycle: turns accumulate in memory per session; a session
ends explicitly (POST /sessions/{id}/end) or after an idle timeout
enforced by a background reaper. Ending a session summarises it via
Ollama, embeds the summary, and persists it to Chroma. Failed
finalisations are retried up to MAX_FINALISE_ATTEMPTS on later reaper
passes, because a summary lost to a transient Ollama hiccup is a
memory lost forever.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from app.config import SERVICE_NAME, SERVICE_VERSION, Settings, get_settings
from app.memory import MemoryStore
from app.models import (
    ConversationRequest,
    ConversationResponse,
    DeleteResponse,
    EndSessionResponse,
    HealthResponse,
    MemoryPage,
    Turn,
)
from app.retriever import build_memory_block, inject_memory, retrieve_memories
from app.summariser import summarise_session

logger = logging.getLogger(__name__)

READINESS_ATTEMPTS = 30
READINESS_DELAY_SECONDS = 2.0
MAX_FINALISE_ATTEMPTS = 3


@dataclass
class SessionState:
    """In-flight conversation state, bounded to the last N turns."""

    turns: deque = field(default_factory=deque)
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    finalise_attempts: int = 0


# --------------------------------------------------------------------- #
# Startup helpers                                                        #
# --------------------------------------------------------------------- #


async def _wait_for_ollama(client: httpx.AsyncClient, settings: Settings) -> None:
    """Block until Ollama answers /api/tags, or raise after the budget.

    Containers race their dependencies on boot; retrying here means
    `docker compose up` succeeds regardless of whether Ollama or this
    service wins the race.
    """
    for attempt in range(1, READINESS_ATTEMPTS + 1):
        try:
            response = await client.get(
                f"{settings.ollama_host}/api/tags",
                timeout=settings.health_timeout_seconds,
            )
            response.raise_for_status()
            logger.info("Ollama reachable at %s", settings.ollama_host)
            return
        except Exception:  # noqa: BLE001 - any failure means "not ready yet"
            logger.info(
                "Waiting for Ollama at %s (attempt %d/%d)",
                settings.ollama_host,
                attempt,
                READINESS_ATTEMPTS,
            )
            await asyncio.sleep(READINESS_DELAY_SECONDS)
    raise RuntimeError(
        f"Ollama unreachable at {settings.ollama_host}. On the host, check "
        "the systemd drop-in binds OLLAMA_HOST=0.0.0.0 (see README, Networking)."
    )


# --------------------------------------------------------------------- #
# Session registry                                                       #
# --------------------------------------------------------------------- #


async def _touch_session(app: FastAPI, session_id: str) -> SessionState:
    """Return the session, creating it on first sight."""
    settings: Settings = app.state.settings
    async with app.state.lock:
        state = app.state.sessions.get(session_id)
        if state is None:
            state = SessionState(turns=deque(maxlen=settings.raw_turns_kept))
            app.state.sessions[session_id] = state
            logger.info("session %s opened", session_id)
        state.last_active = time.time()
        return state


async def _record_turns(app: FastAPI, session_id: str, turns: list[Turn]) -> None:
    """Append turns to a session, bumping its idle clock."""
    state = await _touch_session(app, session_id)
    async with app.state.lock:
        for turn in turns:
            state.turns.append(turn)


def _fingerprint_messages(messages: list[dict]) -> str:
    """Derive a stable session id from an Ollama-style message list.

    Home Assistant's Ollama integration sends the full history on every
    call and no conversation id, so the first user message is the only
    stable anchor for a session. Two conversations opening with the
    same words within one idle window would merge; the README documents
    the trade and the X-Session-Id header opts out of it entirely.
    """
    anchor = ""
    for message in messages:
        if message.get("role") == "user":
            anchor = str(message.get("content", ""))[:512]
            break
    if not anchor and messages:
        anchor = str(messages[0].get("content", ""))[:512]
    digest = hashlib.sha1(anchor.encode("utf-8", errors="replace")).hexdigest()
    return f"ha-{digest[:16]}"


def _last_user_text(messages: list[dict]) -> str:
    """Pull the newest user message as retrieval query text.

    Content may be a plain string or (multimodal) a list of parts;
    only the text parts matter for embedding.
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            ).strip()
    return ""


# --------------------------------------------------------------------- #
# Finalisation                                                           #
# --------------------------------------------------------------------- #


async def _finalise_session(
    app: FastAPI, session_id: str, reason: str
) -> EndSessionResponse:
    """Summarise, embed, and persist one session, then drop it.

    On failure the session is re-queued for the reaper (bounded by
    MAX_FINALISE_ATTEMPTS) rather than lost, because the usual cause
    is a transient Ollama stall that resolves within a minute.
    """
    async with app.state.lock:
        state: SessionState | None = app.state.sessions.pop(session_id, None)
    if state is None:
        return EndSessionResponse(
            session_id=session_id, stored=False, reason="not_found"
        )
    turns = list(state.turns)
    if len(turns) < 2:
        logger.info("session %s dropped: too short to remember", session_id)
        return EndSessionResponse(
            session_id=session_id, stored=False, reason="too_short"
        )

    settings: Settings = app.state.settings
    try:
        summary, tags = await summarise_session(app.state.client, settings, turns)
        from app.retriever import embed_text  # local import avoids a cycle

        embedding = await embed_text(app.state.client, settings, summary)
        memory_id = app.state.store.add_memory(
            session_id=session_id,
            summary=summary,
            tags=tags,
            raw_turns=turns,
            embedding=embedding,
        )
        logger.info(
            "session %s stored as memory %s (%s)", session_id, memory_id, reason
        )
        return EndSessionResponse(
            session_id=session_id, stored=True, memory_id=memory_id, reason=reason
        )
    except Exception as exc:  # noqa: BLE001
        state.finalise_attempts += 1
        if state.finalise_attempts < MAX_FINALISE_ATTEMPTS:
            state.last_active = time.time()
            async with app.state.lock:
                app.state.sessions[session_id] = state
            logger.warning(
                "finalise failed for %s (attempt %d/%d), re-queued: %s",
                session_id,
                state.finalise_attempts,
                MAX_FINALISE_ATTEMPTS,
                exc,
            )
            return EndSessionResponse(
                session_id=session_id, stored=False, reason="retrying"
            )
        logger.error(
            "finalise abandoned for %s after %d attempts: %s",
            session_id,
            MAX_FINALISE_ATTEMPTS,
            exc,
        )
        return EndSessionResponse(
            session_id=session_id, stored=False, reason="failed"
        )


async def _reaper(app: FastAPI) -> None:
    """Background loop that finalises idle sessions."""
    settings: Settings = app.state.settings
    while True:
        try:
            await asyncio.sleep(settings.reaper_interval_seconds)
            now = time.time()
            async with app.state.lock:
                expired = [
                    sid
                    for sid, state in app.state.sessions.items()
                    if now - state.last_active >= settings.session_idle_seconds
                ]
            for session_id in expired:
                await _finalise_session(app, session_id, reason="idle")
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - the reaper must outlive any one failure
            logger.exception("reaper pass failed; continuing")


# --------------------------------------------------------------------- #
# App wiring                                                             #
# --------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start order: logging, Ollama readiness, Chroma, reaper."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.state.settings = settings
    app.state.client = httpx.AsyncClient()
    await _wait_for_ollama(app.state.client, settings)
    # MemoryStore retries internally with blocking sleeps; acceptable in
    # lifespan because no request is served until startup completes.
    app.state.store = MemoryStore(settings)
    app.state.sessions: dict[str, SessionState] = {}
    app.state.lock = asyncio.Lock()
    reaper_task = asyncio.create_task(_reaper(app))
    logger.info("%s %s ready", SERVICE_NAME, SERVICE_VERSION)
    yield
    reaper_task.cancel()
    with suppress(asyncio.CancelledError):
        await reaper_task
    # Shutdown flushes whatever is in flight; a restart should cost
    # idle time, not memories.
    for session_id in list(app.state.sessions.keys()):
        with suppress(Exception):
            await _finalise_session(app, session_id, reason="shutdown")
    await app.state.client.aclose()


app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION, lifespan=lifespan)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Convenience redirect to the interactive docs."""
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Service health plus dependency reachability."""
    settings: Settings = app.state.settings
    chroma_ok = app.state.store.heartbeat()
    try:
        response = await app.state.client.get(
            f"{settings.ollama_host}/api/tags",
            timeout=settings.health_timeout_seconds,
        )
        ollama_ok = response.status_code == 200
    except Exception:  # noqa: BLE001
        ollama_ok = False
    return HealthResponse(
        ok=chroma_ok and ollama_ok,
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        chroma_ok=chroma_ok,
        ollama_ok=ollama_ok,
        memories=app.state.store.count() if chroma_ok else 0,
        active_sessions=len(app.state.sessions),
    )


# --------------------------------------------------------------------- #
# Conversation: explicit-session endpoint                                #
# --------------------------------------------------------------------- #


@app.post("/conversation", response_model=ConversationResponse)
async def conversation(request: ConversationRequest) -> ConversationResponse:
    """One full turn with memory injection, for explicit-session callers."""
    settings: Settings = app.state.settings
    state = await _touch_session(app, request.session_id)
    history = [t.model_dump() for t in state.turns]

    memories = await retrieve_memories(
        app.state.client, settings, app.state.store, request.message
    )
    block = build_memory_block(memories, settings.max_injected_chars)
    messages = inject_memory(
        [*history, {"role": "user", "content": request.message}],
        block,
        settings.system_prompt,
    )

    model = request.model or settings.chat_model
    try:
        response = await app.state.client.post(
            f"{settings.ollama_host}/api/chat",
            json={
                "model": model,
                "stream": False,
                "options": {"num_ctx": settings.num_ctx},
                "messages": messages,
            },
            timeout=settings.generate_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ollama error: {exc}") from exc

    reply = response.json().get("message", {}).get("content", "")
    await _record_turns(
        app,
        request.session_id,
        [
            Turn(role="user", content=request.message),
            Turn(role="assistant", content=reply),
        ],
    )
    return ConversationResponse(
        session_id=request.session_id,
        reply=reply,
        model=model,
        memories_used=memories,
    )


# --------------------------------------------------------------------- #
# Conversation: Ollama-compatible proxy (the Home Assistant path)        #
# --------------------------------------------------------------------- #


@app.post("/api/chat")
async def api_chat(request: Request):
    """Ollama-compatible /api/chat with memory injection.

    Byte-compatible with upstream for both streaming and non-streaming
    callers; the only visible additions are the injected system context
    and an X-Ramone-Memories response header. Home Assistant's Ollama
    integration points here instead of at Ollama and notices nothing.
    """
    settings: Settings = app.state.settings
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    messages = body.get("messages") or []
    session_id = request.headers.get("x-session-id") or _fingerprint_messages(messages)
    query_text = _last_user_text(messages)

    memories = []
    if query_text:
        memories = await retrieve_memories(
            app.state.client, settings, app.state.store, query_text
        )
    block = build_memory_block(memories, settings.max_injected_chars)
    outbound = {**body, "messages": inject_memory(messages, block, settings.system_prompt)}
    extra_headers = {"X-Ramone-Memories": str(len(memories))}

    upstream_url = f"{settings.ollama_host}/api/chat"
    stream = bool(body.get("stream", True))  # Ollama defaults to streaming

    if not stream:
        try:
            response = await app.state.client.post(
                upstream_url,
                json=outbound,
                timeout=settings.generate_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                status_code=502,
                content={"error": f"ollama unreachable at {settings.ollama_host}: {exc}"},
            )
        if response.status_code == 200:
            reply = response.json().get("message", {}).get("content", "")
            await _record_turns(
                app,
                session_id,
                [
                    Turn(role="user", content=query_text),
                    Turn(role="assistant", content=reply),
                ],
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers=extra_headers,
        )

    # Streaming: pass upstream NDJSON through untouched while teeing the
    # assistant tokens so the turn can be recorded once the stream ends.
    try:
        upstream_request = app.state.client.build_request(
            "POST",
            upstream_url,
            json=outbound,
            timeout=settings.generate_timeout_seconds,
        )
        upstream = await app.state.client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": f"ollama unreachable at {settings.ollama_host}: {exc}"},
        )

    if upstream.status_code != 200:
        content = await upstream.aread()
        await upstream.aclose()
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    async def relay():
        collected: list[str] = []
        try:
            async for line in upstream.aiter_lines():
                if not line:
                    continue
                yield line + "\n"
                with suppress(json.JSONDecodeError):
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content")
                    if piece:
                        collected.append(piece)
        finally:
            await upstream.aclose()
            # Record even on client disconnect: the user said it, the
            # model (partially) answered it, the session should know.
            if query_text and collected:
                with suppress(Exception):
                    await _record_turns(
                        app,
                        session_id,
                        [
                            Turn(role="user", content=query_text),
                            Turn(role="assistant", content="".join(collected)),
                        ],
                    )

    return StreamingResponse(
        relay(), media_type="application/x-ndjson", headers=extra_headers
    )


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def api_passthrough(path: str, request: Request):
    """Transparent proxy for every other Ollama endpoint.

    /api/tags and /api/version are what Home Assistant's integration
    calls during setup; the rest ride along so the proxy never becomes
    the reason an Ollama client breaks. Non-streaming by design; the
    streaming path this service exists for is /api/chat above.
    """
    settings: Settings = app.state.settings
    url = f"{settings.ollama_host}/api/{path}"
    try:
        if request.method == "GET":
            response = await app.state.client.get(
                url, timeout=settings.generate_timeout_seconds
            )
        else:
            response = await app.state.client.post(
                url,
                content=await request.body(),
                headers={
                    "content-type": request.headers.get(
                        "content-type", "application/json"
                    )
                },
                timeout=settings.generate_timeout_seconds,
            )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": f"ollama unreachable at {settings.ollama_host}: {exc}"},
        )
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
    )


# --------------------------------------------------------------------- #
# Memory management                                                      #
# --------------------------------------------------------------------- #


@app.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
async def end_session(session_id: str) -> EndSessionResponse:
    """Finalise a session now instead of waiting for the idle reaper."""
    return await _finalise_session(app, session_id, reason="explicit")


def _parse_iso(value: str | None, param: str) -> float | None:
    """Parse an ISO date/datetime query param to a UTC timestamp."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{param} must be ISO 8601, e.g. 2026-07-01 or 2026-07-01T12:00:00",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


@app.get("/memories", response_model=MemoryPage)
def list_memories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    start: str | None = Query(None, description="ISO date/datetime, inclusive"),
    end: str | None = Query(None, description="ISO date/datetime, inclusive"),
) -> MemoryPage:
    """Stored memories, newest first, filterable by date range."""
    start_ts = _parse_iso(start, "start")
    end_ts = _parse_iso(end, "end")
    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise HTTPException(status_code=400, detail="start must not be after end")
    items, total = app.state.store.list_memories(page, page_size, start_ts, end_ts)
    return MemoryPage(items=items, page=page, page_size=page_size, total=total)


@app.delete("/memories/{memory_id}", response_model=DeleteResponse)
def delete_memory(memory_id: str) -> DeleteResponse:
    """Remove one memory permanently."""
    if not app.state.store.delete(memory_id):
        raise HTTPException(status_code=404, detail=f"no memory with id {memory_id}")
    return DeleteResponse(id=memory_id, deleted=True)
