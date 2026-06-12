# oneview_to_netbox.py

Sync HP OneView enclosures and server hardware into [NetBox](https://netbox.dev) as Device records. Blade servers are automatically linked to their parent chassis via DeviceBays.

## What it syncs

| OneView resource | NetBox result |
|---|---|
| Enclosure | Device (role=Chassis) + one DeviceBay per blade slot |
| Blade server | Device (role=Server) installed in the matching chassis DeviceBay |
| Rack-mount server | Device (role=Server) standalone |

Auto-created on first run if absent: `Manufacturer` (Hewlett Packard Enterprise), `DeviceType` per model name, `DeviceRole` for chassis and server role names.

## Requirements

```
pip install pynetbox requests
```

HP OneView 5.x – 8.x is supported. The API version is auto-detected at startup; override with `--oneview-api-version` if needed.

## NetBox prerequisites

- A **Site** must already exist in NetBox. All synced devices are placed there.
- If using `--tenant`, the **Tenant** must already exist in NetBox.
- The API token needs permission to create/update/delete under **DCIM** (devices, device types, device roles, manufacturers, device bays).

## Usage

### Preview changes (dry-run first)

```bash
python oneview_to_netbox.py \
    --oneview-host  https://oneview.example.com \
    --oneview-user  Administrator \
    --oneview-password SECRET \
    --netbox-url    https://netbox.example.com \
    --token         YOUR_API_TOKEN \
    --site          my-datacenter \
    --no-ssl-verify \
    --dry-run
```

### Full sync

```bash
python oneview_to_netbox.py \
    --oneview-host  https://oneview.example.com \
    --oneview-user  Administrator \
    --oneview-password SECRET \
    --netbox-url    https://netbox.example.com \
    --token         YOUR_API_TOKEN \
    --site          my-datacenter \
    --no-ssl-verify
```

### Sync with a tenant

```bash
python oneview_to_netbox.py \
    --oneview-host  https://oneview.example.com \
    --oneview-user  Administrator \
    --oneview-password SECRET \
    --netbox-url    https://netbox.example.com \
    --token         YOUR_API_TOKEN \
    --site          my-datacenter \
    --tenant        my-tenant \
    --no-ssl-verify
```

### Sync with stale-device cleanup

Preview what would be deleted, then apply:

```bash
# 1. Dry-run to see everything — syncs and deletions
python oneview_to_netbox.py ... --delete-missing --dry-run

# 2. Apply
python oneview_to_netbox.py ... --delete-missing
```

### Chassis only / servers only

```bash
# Re-sync enclosures and rebuild device bays without touching servers
python oneview_to_netbox.py ... --skip-servers

# Re-sync servers without touching chassis
python oneview_to_netbox.py ... --skip-chassis
```

## All options

### HP OneView

| Flag | Default | Description |
|---|---|---|
| `--oneview-host` | required | OneView appliance URL |
| `--oneview-user` | required | OneView username |
| `--oneview-password` | required | OneView password |
| `--oneview-api-version N` | auto-detect | Override REST API version (e.g. `2400`, `3600`) |

### NetBox

| Flag | Default | Description |
|---|---|---|
| `--netbox-url` | required | NetBox base URL (no trailing slash) |
| `--token` | required | NetBox API token |
| `--site` | required | Site name where devices will be placed (must already exist) |
| `--tenant NAME` | none | Tenant name or slug to assign to all synced devices (must already exist) |

### Device roles

| Flag | Default | Description |
|---|---|---|
| `--chassis-role NAME` | `Chassis` | DeviceRole name for enclosures |
| `--server-role NAME` | `Server` | DeviceRole name for servers |

### Behaviour

| Flag | Default | Description |
|---|---|---|
| `--no-ssl-verify` | off | Disable TLS verification for OneView (common in lab environments) |
| `--skip-chassis` | off | Skip enclosure sync |
| `--skip-servers` | off | Skip server hardware sync |
| `--delete-missing` | off | Delete NetBox devices (matching role + site) absent from OneView |
| `--dry-run` | off | Show field-level diffs without writing anything |

## Behaviour notes

**Upsert semantics** — devices are matched by name within the site. Existing records are updated only when serial or status differs; unchanged records are skipped.

**Device bays** — bays are named `Bay 1`, `Bay 2`, … up to the enclosure's `deviceBayCount`. New bays are added on each run; existing bays are never removed.

**Power state mapping** — OneView `On` → NetBox `active`; anything else → `offline`.

**`--delete-missing` safety** — only devices with the configured chassis or server role at the configured site are candidates for deletion. Devices added manually or from other sources are not affected. Servers are always deleted before chassis so blades are cleanly vacated from their bays first. `--delete-missing` is ignored for any phase skipped with `--skip-chassis` or `--skip-servers`.

**Chassis not synced in same run** — if servers are synced without chassis (e.g. `--skip-chassis`), blade-to-bay linking is skipped with a warning. Re-run including chassis to establish links.

**NetBox version** — the script uses the `role` field name introduced in NetBox 4.0. On NetBox 3.x the equivalent field is `device_role`; adjust line 330 / 410 of the script if needed.
