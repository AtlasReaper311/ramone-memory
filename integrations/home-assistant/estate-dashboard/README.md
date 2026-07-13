# Atlas Systems Home Assistant estate dashboard

This integration adds read-only REST sensors and a Lovelace dashboard for the Atlas Systems control plane.

It reads public data from:

- `/sonify` for current service health
- `/v1/stats` for estate and sentinel state
- `/v1/slo` for raw probe observations
- `/quota` for Cloudflare allowance position
- `/notify/recent` for recent estate events

No Home Assistant secret or Atlas API credential is required.

## Install

Copy `atlas_estate_package.yaml` to:

```text
/config/packages/atlas_estate_package.yaml
```

Enable packages in `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Copy `atlas_estate_dashboard.yaml` to:

```text
/config/dashboards/atlas_estate_dashboard.yaml
```

Register the YAML dashboard in `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    atlas-estate:
      mode: yaml
      title: Atlas Estate
      icon: mdi:server-network
      filename: dashboards/atlas_estate_dashboard.yaml
```

Restart Home Assistant after the first installation. REST sensors are not created by a YAML reload alone.

The quota sensor remains unavailable until `atlas-quota-watch` is deployed. The other sensors continue to operate independently.
