#!/usr/bin/env python3
"""Render the ten Phase 9 Home Assistant sensors from a local fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "atlas-control-plane/control-plane-summary/v1"
STATES = {"healthy", "warning", "failed", "stale", "unavailable", "unknown"}
ENTITY_IDS = (
    "sensor.atlas_estate_health",
    "sensor.atlas_failed_journeys",
    "sensor.atlas_release_state",
    "sensor.atlas_contract_drift",
    "sensor.atlas_quota_level",
    "sensor.atlas_quota_projection",
    "sensor.atlas_open_gardener_prs",
    "sensor.atlas_secret_hygiene",
    "sensor.atlas_backup_freshness",
    "sensor.atlas_latest_evidence",
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _state(value: Any, default: str = "unknown") -> str:
    return value if value in STATES else default


def _count(value: Any) -> int | str:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "unknown"


def _number(value: Any) -> int | float | str:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else "unknown"


def _bounded_attributes(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source}


def unavailable_entities() -> dict[str, dict[str, Any]]:
    """Return a bounded unavailable rendering for an incompatible document."""
    return {
        entity_id: {"state": "unavailable", "attributes": {"control_plane_state": "unavailable"}}
        for entity_id in ENTITY_IDS
    }


def render(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Render sensor state/attributes without performing any network call."""
    if summary.get("schema_version") != SCHEMA_VERSION:
        return unavailable_entities()

    health = _mapping(summary.get("health"))
    journeys = _mapping(summary.get("journeys"))
    release = _mapping(summary.get("release"))
    registry = _mapping(summary.get("contract_registry"))
    quota = _mapping(summary.get("quota"))
    gardener = _mapping(summary.get("gardener_proposals"))
    secrets = _mapping(summary.get("secret_hygiene"))
    backups = _mapping(summary.get("backups"))
    evidence = _mapping(summary.get("evidence"))

    rendered = {
        "sensor.atlas_estate_health": {
            "state": _state(summary.get("state")),
            "attributes": _bounded_attributes(
                health,
                ("state", "components_total", "components_healthy", "active_incidents"),
            ),
        },
        "sensor.atlas_failed_journeys": {
            "state": _count(journeys.get("failed")),
            "attributes": _bounded_attributes(journeys, ("state", "total")),
        },
        "sensor.atlas_release_state": {
            "state": _state(release.get("state")),
            "attributes": _bounded_attributes(
                release,
                ("repository", "environment", "commit", "completed_at", "evidence_ref"),
            ),
        },
        "sensor.atlas_contract_drift": {
            "state": _count(registry.get("drift_count")),
            "attributes": _bounded_attributes(
                registry, ("state", "contracts_total", "contracts_valid")
            ),
        },
        "sensor.atlas_quota_level": {
            "state": _state(quota.get("state")),
            "attributes": _bounded_attributes(
                quota,
                ("used_percent", "projected_percent", "highest_meter", "period_ends_at"),
            ),
        },
        "sensor.atlas_quota_projection": {
            "state": _number(quota.get("projected_percent")),
            "attributes": _bounded_attributes(
                quota, ("state", "used_percent", "highest_meter", "period_ends_at")
            ),
        },
        "sensor.atlas_open_gardener_prs": {
            "state": _count(gardener.get("open_pull_requests")),
            "attributes": _bounded_attributes(
                gardener, ("state", "total", "validation_failed")
            ),
        },
        "sensor.atlas_secret_hygiene": {
            "state": _state(secrets.get("state")),
            "attributes": _bounded_attributes(
                secrets, ("required", "present", "stale", "unknown")
            ),
        },
        "sensor.atlas_backup_freshness": {
            "state": _state(backups.get("state")),
            "attributes": _bounded_attributes(
                backups, ("total", "healthy", "stale", "failed", "unknown")
            ),
        },
        "sensor.atlas_latest_evidence": {
            "state": evidence.get("newest_record_at") or "unknown",
            "attributes": _bounded_attributes(
                evidence, ("state", "searchable_records", "expiring_soon")
            ),
        },
    }
    for entity in rendered.values():
        entity["attributes"].setdefault(
            "control_plane_state",
            _state(entity["attributes"].pop("state", summary.get("state"))),
        )
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.fixture.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("ERROR: fixture must contain a JSON object", file=sys.stderr)
        return 1
    print(json.dumps(render(document), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
