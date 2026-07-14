from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(INTEGRATION))

import render_fixture

EXPECTED_ENTITIES = set(render_fixture.ENTITY_IDS)


class AtlasControlPlaneSensorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = (INTEGRATION / "atlas_control_plane_package.yaml").read_text(
            encoding="utf-8"
        )
        cls.dashboard = (
            INTEGRATION / "atlas_control_plane_dashboard.yaml"
        ).read_text(encoding="utf-8")
        cls.fixture_path = INTEGRATION / "fixtures" / "control-plane-summary.json"
        cls.fixture = json.loads(cls.fixture_path.read_text(encoding="utf-8"))

    def test_fixture_renders_exactly_ten_bounded_entities(self) -> None:
        rendered = render_fixture.render(self.fixture)
        self.assertEqual(EXPECTED_ENTITIES, set(rendered))
        self.assertEqual(10, len(rendered))
        self.assertEqual(1, rendered["sensor.atlas_failed_journeys"]["state"])
        self.assertEqual(2, rendered["sensor.atlas_contract_drift"]["state"])
        self.assertEqual(88.2, rendered["sensor.atlas_quota_projection"]["state"])
        self.assertEqual(1, rendered["sensor.atlas_open_gardener_prs"]["state"])
        for entity in rendered.values():
            self.assertIn("control_plane_state", entity["attributes"])
            self.assertLessEqual(len(json.dumps(entity)), 2048)

    def test_missing_data_is_unknown_not_healthy(self) -> None:
        rendered = render_fixture.render({"schema_version": render_fixture.SCHEMA_VERSION})
        for entity in rendered.values():
            self.assertNotEqual("healthy", entity["state"])
            self.assertIn(entity["state"], {"unknown", "unavailable"})

    def test_incompatible_schema_is_unavailable(self) -> None:
        rendered = render_fixture.render({"schema_version": "future/v2"})
        self.assertTrue(all(item["state"] == "unavailable" for item in rendered.values()))

    def test_package_declares_exact_sensor_names(self) -> None:
        unique_ids = set(re.findall(r"^\s+unique_id:\s+([a-z0-9_]+)$", self.package, re.MULTILINE))
        self.assertEqual(
            EXPECTED_ENTITIES, {f"sensor.{unique_id}" for unique_id in unique_ids}
        )
        self.assertEqual(10, len(unique_ids))

    def test_package_and_dashboard_declare_no_service_calls_or_controls(self) -> None:
        combined = f"{self.package}\n{self.dashboard}".lower()
        for forbidden in (
            "service:",
            "rest_command:",
            "automation:",
            "script:",
            "button:",
            "switch:",
            "light:",
            "tap_action:",
            "hold_action:",
        ):
            self.assertNotIn(forbidden, combined)
        for credential_marker in ("authorization:", "password:", "!secret", "bearer "):
            self.assertNotIn(credential_marker, combined)

    def test_dashboard_uses_exactly_the_ten_phase9_entities(self) -> None:
        dashboard_entities = set(
            re.findall(r"entity:\s+(sensor\.[a-z0-9_]+)", self.dashboard)
        )
        self.assertEqual(EXPECTED_ENTITIES, dashboard_entities)

    def test_protected_runtime_and_legacy_files_are_unchanged(self) -> None:
        manifest = json.loads(
            (INTEGRATION / "protected-files.sha256.json").read_text(encoding="utf-8")
        )
        for relative, expected in manifest["files"].items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(expected, actual, relative)
        unavailable = " ".join(manifest["unavailable_live_sources"]).lower()
        for term in ("openwebui", "phone", "watch", "specular", "wyoming", "stt", "tts"):
            self.assertIn(term, unavailable)

    def test_fixture_cli_is_deterministic(self) -> None:
        command = [
            sys.executable,
            str(INTEGRATION / "render_fixture.py"),
            "--fixture",
            str(self.fixture_path),
        ]
        first = subprocess.run(command, check=True, capture_output=True).stdout
        second = subprocess.run(command, check=True, capture_output=True).stdout
        self.assertEqual(first, second)
        self.assertEqual(EXPECTED_ENTITIES, set(json.loads(first)))

    def test_documentation_keeps_rollout_disabled_by_default(self) -> None:
        documentation = (INTEGRATION / "README.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "disabled-by-default",
            "excluded from assist",
            "no live home assistant change",
            "rollback",
        ):
            self.assertIn(phrase, documentation)


if __name__ == "__main__":
    unittest.main()
