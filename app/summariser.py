"""Session summarisation and tag extraction via Ollama.

A finished session is compressed into one to three sentences plus a
handful of tags. Ollama's JSON mode (format: "json") constrains the
output shape, but the parse is still defensive: a local model under
memory pressure can and will produce almost-JSON, and a lost summary
is worse than a slightly ragged one.
"""

import json
import logging
import re

import httpx

from app.config import Settings
from app.models import Turn

logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 600
TAGS_MAX = 5
# Turns are clipped before summarisation so one pasted wall of text
# cannot blow the summariser's context window.
TURN_CLIP_CHARS = 1500

_SYSTEM_PROMPT = (
    "You compress finished conversations into long-term memory for a "
    "local assistant called Ramone. Reply with strict JSON only, shaped "
    'exactly as {"summary": string, "tags": [string]}. The summary is '
    "one to three sentences, third person, concrete: keep names, "
    "decisions, preferences, and numbers. Tags are two to five lowercase "
    "single words or short hyphenated phrases. No markdown, no "
    "commentary, no keys beyond summary and tags."
)


def _render_transcript(turns: list[Turn]) -> str:
    """Flatten turns into a plain transcript the model can compress."""
    lines = []
    for turn in turns:
        content = turn.content.strip()
        if len(content) > TURN_CLIP_CHARS:
            content = content[:TURN_CLIP_CHARS].rstrip() + " \u2026"
        lines.append(f"{turn.role}: {content}")
    return "\n".join(lines)


def _parse_summary_payload(raw: str) -> tuple[str, list[str]]:
    """Extract (summary, tags) from model output, degrading gracefully.

    Three tiers: strict JSON, first JSON object found anywhere in the
    text, then the raw text itself as the summary with no tags. The
    memory survives every tier.
    """
    candidate = raw.strip()
    parsed: dict | None = None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None
    if not isinstance(parsed, dict):
        logger.warning("summariser returned non-JSON; storing raw text")
        return candidate[:SUMMARY_MAX_CHARS], []

    summary = str(parsed.get("summary", "")).strip()[:SUMMARY_MAX_CHARS]
    tags_raw = parsed.get("tags", [])
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for tag in tags_raw:
            # Tags are persisted comma-joined in Chroma metadata, so a
            # comma inside a tag would corrupt the round trip.
            clean = str(tag).strip().lower().replace(",", " ").strip()
            if clean and clean not in tags:
                tags.append(clean)
            if len(tags) == TAGS_MAX:
                break
    return summary, tags


async def summarise_session(
    client: httpx.AsyncClient, settings: Settings, turns: list[Turn]
) -> tuple[str, list[str]]:
    """Compress a finished session into (summary, tags) via Ollama."""
    transcript = _render_transcript(turns)
    response = await client.post(
        f"{settings.ollama_host}/api/chat",
        json={
            "model": settings.chat_model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": settings.temperature,
                "num_ctx": settings.num_ctx,
            },
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
        },
        timeout=settings.generate_timeout_seconds,
    )
    response.raise_for_status()
    raw = response.json().get("message", {}).get("content", "")
    summary, tags = _parse_summary_payload(raw)
    if not summary:
        raise RuntimeError("summariser produced an empty summary")
    return summary, tags
