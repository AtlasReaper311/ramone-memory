<div align="center">
  <img src="https://raw.githubusercontent.com/AtlasReaper311/AtlasReaper311/main/atlas-icon-dark-256.png" width="88" alt="Atlas Systems"/>
</div>

# ramone-memory

```
┌─────────────────────────────────────────────┐
│  ATLAS SYSTEMS // ramone-memory             │
│  long-term memory for ramone: sessions      │
│  distilled, embedded, recalled on demand    │
└─────────────────────────────────────────────┘
```

![Python](https://img.shields.io/badge/python-3.12-f5a623?style=flat-square&labelColor=0a0a0f)
![FastAPI](https://img.shields.io/badge/api-fastapi-4ade80?style=flat-square&labelColor=0a0a0f)
![Vector store](https://img.shields.io/badge/vectors-chromadb-aaa9a0?style=flat-square&labelColor=0a0a0f)
![Cost](https://img.shields.io/badge/cost-%C2%A30-aaa9a0?style=flat-square&labelColor=0a0a0f)

A memory layer that sits between Home Assistant and Ollama. Every finished conversation is summarised by a local model, embedded, and persisted; every new conversation starts by retrieving the three most relevant past memories and injecting them into the system prompt before Ollama sees a word. Ramone stops forgetting when the session ends.

```
HA / any Ollama client
   │  POST /api/chat  (byte-compatible, streaming included)
   ▼
ramone-memory ──▶ embed query ──▶ ChromaDB top-3 ──▶ inject into system prompt
   │                                                        │
   └──▶ Ollama /api/chat (host GPU) ◀───────────────────────┘
   session idle / explicit end
   └──▶ summarise (llama3.1:8b) ──▶ embed (nomic-embed-text) ──▶ store
```

## Prerequisites

- Docker Desktop (Windows or macOS) or Docker Engine with the compose plugin (Linux/WSL2)
- [Ollama](https://ollama.com) running on the host, bound to `0.0.0.0` (systemd drop-in on SPECULAR-CORE), with two models:

  ```bash
  ollama pull nomic-embed-text
  ollama pull llama3.1:8b
  ```

Ollama stays on the host rather than in compose because it owns the GPU; the container only needs HTTP access to it.

## Setup

```bash
cp .env.example .env        # optional: defaults work out of the box
docker compose up --build -d
docker compose logs -f memory
```

First boot waits for Ollama and Chroma, then serves on port 8091. Verify:

```bash
curl -sS http://localhost:8091/health
```

```powershell
Invoke-RestMethod http://localhost:8091/health
```

### Pointing Home Assistant at it

The service speaks Ollama's own API, so the change is a URL swap, not a new integration. In Settings, Devices and Services, Ollama, reconfigure the URL:

| Field | Before | After |
|---|---|---|
| URL | `http://host.docker.internal:11434` | `http://host.docker.internal:8091` |

HA keeps its model choice, prompt, and options; memory injection and session capture happen invisibly in between. Everything HA calls during setup (`/api/tags`, `/api/version`) passes straight through. When the voice stack moves to Specular Node, the URL becomes the node's static LAN IP with the same port; nothing else changes.

## Usage

Talk through it exactly as if it were Ollama:

```bash
curl -sS http://localhost:8091/api/chat -d '{
  "model": "llama3.1:8b",
  "stream": false,
  "messages": [{"role": "user", "content": "What did we decide about the NAS?"}]
}'
```

The `X-Ramone-Memories` response header reports how many memories were injected. Explicit-session callers get a simpler shape:

```bash
curl -sS http://localhost:8091/conversation \
  -H "Content-Type: application/json" \
  -d '{"session_id": "workbench", "message": "Morning. Where were we?"}'
```

Inspect, filter, and prune what Ramone remembers:

```bash
curl -sS "http://localhost:8091/memories?page=1&page_size=10"
curl -sS "http://localhost:8091/memories?start=2026-06-01&end=2026-06-30"
curl -sS -X POST http://localhost:8091/sessions/workbench/end
curl -sS -X DELETE http://localhost:8091/memories/<memory_id>
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chat` | Ollama-compatible chat with memory injection (streaming and non-streaming) |
| `GET/POST` | `/api/*` | Transparent passthrough for every other Ollama endpoint |
| `POST` | `/conversation` | One full turn with an explicit `session_id` |
| `POST` | `/sessions/{id}/end` | Finalise a session into long-term memory now |
| `GET` | `/memories` | Paginated memories, newest first, `start`/`end` date filters |
| `DELETE` | `/memories/{id}` | Remove one memory permanently |
| `GET` | `/health` | Service, Chroma, and Ollama status plus memory count |

## Design notes

**What a memory is.** One Chroma document per finished session: the distilled summary as the document text, with session id, timestamp, model-extracted tags, and the last eight raw turns in metadata. Retrieval is semantic over the summaries; the raw turns exist for audit, not for injection.

**When a session ends.** Ollama's API has no end-of-conversation event, so a session finalises either explicitly (`POST /sessions/{id}/end`, wired to an HA automation if wanted) or after 300 idle seconds via a background reaper. Failed finalisations re-queue up to three times, because the usual cause is a transient Ollama stall.

**Session identity on `/api/chat`.** HA sends full history and no conversation id, so sessions are fingerprinted from the first user message. Two conversations opening with identical words inside one idle window would merge; send an `X-Session-Id` header to opt out of fingerprinting entirely.

**Short-term vs long-term.** [`ollama-rag-kit`](https://github.com/AtlasReaper311/ollama-rag-kit)'s `ramone_sessions` collection is rolling short-term context with a TTL. This repo is the other half: permanent, distilled, cross-session memory in its own `ramone_memory` collection. The two never touch the same data.

## How it fits into Atlas Systems

This is the memory organ of the Ramone subsystem. It fronts the same host Ollama that [`ollama-rag-kit`](https://github.com/AtlasReaper311/ollama-rag-kit) uses for RAG and shares its embedding model with [`atlas-corpus`](https://github.com/AtlasReaper311/atlas-corpus), so one `nomic-embed-text` pull serves the whole estate. [`atlas-bootstrap`](https://github.com/AtlasReaper311/atlas-bootstrap) starts it as part of environment reconstruction, and the public face of Ramone at [`ramone-edge`](https://github.com/AtlasReaper311/ramone-edge) remains untouched: public Q&A and private memory are deliberately separate surfaces.

Durable state belongs in a store with an API, not in a context window; a proxy that enriches a protocol without breaking its contract can add a capability to a system that never asked for one.

---

Part of [atlas-systems.uk](https://atlas-systems.uk)
