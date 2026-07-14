# Atlas control-plane sensors for Home Assistant

Status: disabled-by-default Phase 9 repository example. Nothing in this
directory is installed, loaded, exposed to Assist, or applied automatically.

## Boundary

One public `ControlPlaneSummary` request fans out to exactly ten read-only
sensors:

- `sensor.atlas_estate_health`
- `sensor.atlas_failed_journeys`
- `sensor.atlas_release_state`
- `sensor.atlas_contract_drift`
- `sensor.atlas_quota_level`
- `sensor.atlas_quota_projection`
- `sensor.atlas_open_gardener_prs`
- `sensor.atlas_secret_hygiene`
- `sensor.atlas_backup_freshness`
- `sensor.atlas_latest_evidence`

The package declares no automation, script, button, switch, light, REST
command, or Home Assistant service call. It needs no Home Assistant token and
contains no Atlas credential. It does not change which entities Assist can
see.

## Fixture mode

Render the committed fixture locally without Home Assistant or a network:

```bash
python3 integrations/home-assistant/atlas-control-plane/render_fixture.py \
  --fixture integrations/home-assistant/atlas-control-plane/fixtures/control-plane-summary.json
```

The output is a deterministic mapping of the ten entity IDs to bounded state
and attributes. Missing fields become `unknown`; an incompatible summary
becomes `unavailable`. Neither condition becomes healthy.

Run the standard-library tests:

```bash
python3 -m unittest discover \
  -s integrations/home-assistant/atlas-control-plane/tests -v
```

`protected-files.sha256.json` snapshots the existing memory proxy, compose
stack, and legacy dashboard/package files. The tests fail if this Phase 9
branch changes those protected repository-owned paths. Live OpenWebUI,
phone/watch, SPECULAR-tool, Assist, and Wyoming configuration is unavailable
in Git and must be inspected separately before enablement.

## Dashboard example

`atlas_control_plane_dashboard.yaml` uses standard Lovelace cards only. The
three small views stack naturally on mobile and stay readable on desktop. It
contains no controls and is an example, not an automatic install.

## Owner-gated rollout

Do not run these steps until the repository pull requests are reviewed, the
summary route is deployed separately, and the owner has completed the
read-only live inventory and configuration backup described in
`atlas-infra/docs/ramone-home-assistant-integration.md`.

1. Confirm whether the existing legacy estate package is installed and how
   Home Assistant registered `sensor.atlas_estate_health`.
2. Do not load this package beside the legacy package while that overlapping
   entity/unique ID is active. Choose and review a migration plan first.
3. Copy `atlas_control_plane_package.yaml` into the owner-approved Home
   Assistant packages directory. Do not alter the repository example in
   place.
4. Run Home Assistant's configuration check. Stop on any warning, duplicate
   unique ID, or schema error.
5. Add `atlas_control_plane_dashboard.yaml` as a separate YAML dashboard only
   after the package is valid.
6. Keep every new entity excluded from Assist. Exposure of exactly these ten
   sensors is a later explicit owner action after text/dashboard validation.
7. Verify all six control-plane states, API outage behavior, and mobile and
   desktop layouts before considering a restart or reload. This phase performs
   neither.

## Rollback

1. Disable or remove only the Atlas control-plane package include and its
   dashboard registration.
2. Leave the existing estate package, Ramone conversation entity, memory
   proxy, tool groups, lights/device controls, phone/watch tools, SPECULAR
   tools, wake word, Wyoming STT/TTS, and spoken-response path untouched.
3. Run Home Assistant's configuration check before any owner-approved reload
   or restart.
4. Revert the focused repository pull request if the example itself needs to
   be withdrawn.

No live Home Assistant change, restart, reload, Assist exposure, or entity
migration is part of Phase 9 repository implementation.
