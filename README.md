# nmap_to_netbox

Synchronize nmap scan results into [NetBox](https://netbox.dev) IPAM. Discovers live hosts, resolves reverse DNS, and upserts IP address records — without clobbering reserved or manually-managed entries.

## Tools

| Script | Purpose |
|---|---|
| `nmap_to_netbox.py` | Parse a single nmap XML file and import results into NetBox |
| `scan_tagged_prefixes.py` | Orchestrate scanning of all NetBox prefixes carrying a given tag |
| `oneview_to_netbox.py` | Sync HP OneView enclosures and server hardware into NetBox as Devices |

## Requirements

```
pip install pynetbox
nmap  # must be in PATH
```

## Usage

### Single prefix

```bash
# 1. Scan
nmap -sn -R --dns-servers 8.8.8.8 192.168.1.0/24 -oX scan.xml

# 2. Preview changes (dry-run)
python nmap_to_netbox.py \
    --xml scan.xml \
    --netbox-url https://netbox.example.com \
    --token YOUR_TOKEN \
    --prefix 192.168.1.0/24 \
    --dry-run

# 3. Import
python nmap_to_netbox.py \
    --xml scan.xml \
    --netbox-url https://netbox.example.com \
    --token YOUR_TOKEN \
    --prefix 192.168.1.0/24
```

### All tagged prefixes

Tag prefixes in NetBox with `nmap-scan`, then:

```bash
# Scan all tagged prefixes and import results
python scan_tagged_prefixes.py \
    --netbox-url https://netbox.example.com \
    --token YOUR_TOKEN

# Dry-run: real nmap scans, diff against NetBox without writing
python scan_tagged_prefixes.py \
    --netbox-url https://netbox.example.com \
    --token YOUR_TOKEN \
    --dry-run
```

## Key options

| Flag | Default | Description |
|---|---|---|
| `--reserve-head N` | 20 | Mark first N IPs per prefix as reserved (gateway, etc.) |
| `--reserve-tail M` | 5 | Mark last M IPs per prefix as reserved (broadcast, etc.) |
| `--scan-tag TAG` | `nmap-scan` | Tag applied to every imported IP (auto-created) |
| `--include-down` | off | Import down hosts with status `deprecated` |
| `--live-rdns` | off | Perform live PTR lookups for IPs missing rDNS in XML |
| `--dns-suffix SUFFIX` | — | Append domain suffix to rDNS names |
| `--dry-run` | off | Show diffs without writing to NetBox |

## Behaviour

- **Protected statuses** — IPs with status `reserved`, `dhcp`, or `slaac` in NetBox are never overwritten by scan data.
- **Idempotent** — safe to run repeatedly; unchanged records are skipped.
- **Field-level diffs** — dry-run shows exactly which fields would change, with ANSI colour output in a TTY.
- **Prefix reservation** — head and tail IPs are automatically marked `reserved` on each run.

---

## OneView → NetBox (`oneview_to_netbox.py`)

Syncs HP OneView enclosures and server hardware into NetBox as Device records.

- Enclosures → chassis Devices with DeviceBays for each blade slot
- Blade servers linked to their parent chassis DeviceBay
- Rack servers created as standalone Devices
- Auto-creates Manufacturers, DeviceTypes, and DeviceRoles as needed
- Per-device site/tenant override via OneView labels

### Requirements

```
pip install pynetbox requests
```

### Usage

```bash
python oneview_to_netbox.py \
    --oneview-host  https://oneview.example.com \
    --oneview-user  Administrator \
    --netbox-url    https://netbox.example.com \
    --token         YOUR_API_TOKEN \
    --site          my-datacenter

# Password is prompted securely if --oneview-password is omitted

# Dry-run: preview all changes without writing
python oneview_to_netbox.py ... --dry-run
```

### Key options

| Flag | Default | Description |
|---|---|---|
| `--oneview-password` | _(prompted)_ | OneView password; prompted via stdin if omitted |
| `--oneview-api-version` | auto-detect | OneView REST API version |
| `--site` | `Unknown` | NetBox site for devices; auto-created if it doesn't exist |
| `--tenant` | `Unknown` | NetBox tenant for devices; auto-created if it doesn't exist |
| `--chassis-role` | `Chassis` | DeviceRole name for enclosures |
| `--server-role` | `Server` | DeviceRole name for servers |
| `--device-types-file` | — | YAML file mapping OneView model names to NetBox DeviceType definitions |
| `--label-site` | off | Match device labels against NetBox site names/slugs to override the default site |
| `--label-tenant` | off | Match device labels against NetBox tenant names/slugs to override the default tenant |
| `--chassis-filter NAME …` | — | Only sync chassis whose name contains one of these strings (case-insensitive) |
| `--server-filter NAME …` | — | Only sync servers whose name contains one of these strings (case-insensitive) |
| `--skip-chassis` | off | Skip enclosure sync |
| `--skip-servers` | off | Skip server hardware sync |
| `--delete-missing` | off | Delete NetBox devices absent from OneView (use `--dry-run` first) |
| `--no-ssl-verify` | off | Disable TLS verification (lab use) |
| `--dry-run` | off | Show field-level diffs without writing to NetBox |

### Label-based site/tenant mapping

By default every device is placed in the `Unknown` site and `Unknown` tenant (both auto-created if absent). Enable `--label-site` and/or `--label-tenant` to let OneView labels override these per device. Each label is matched against NetBox site/tenant names and slugs — first match wins.

```bash
# Devices with label "DCWest" go to site=DCWest; label "ACME" goes to tenant=ACME.
# Devices with no matching label fall back to site=Unknown / tenant=Unknown.
python oneview_to_netbox.py ... --label-site --label-tenant
```

**Label format** — OneView labels are alphanumeric only, so the label name must match the NetBox site or tenant name or slug directly:

| OneView label | Matches NetBox site/tenant |
|---|---|
| `London` | name `London` or slug `london` |
| `ACME` | name `ACME` or slug `acme` |
| `DCWest` | name `DCWest` or slug `dc-west` |
| `DC1` | name `DC1` or slug `dc1` |

### Targeted sync

Use `--chassis-filter` and `--server-filter` to limit a run to specific devices by name (case-insensitive substring match). Multiple values are OR'd together.

```bash
# Update a single server
python oneview_to_netbox.py ... --server-filter web01

# Update all chassis whose name contains "blade"
python oneview_to_netbox.py ... --chassis-filter blade

# Update specific servers and a specific chassis in one run
python oneview_to_netbox.py ... --server-filter db01 db02 --chassis-filter encl-a
```

### Behaviour

- **Idempotent** — safe to run repeatedly; unchanged records are skipped.
- **Server names** — uses the OS hostname (`serverName`) in preference to the OneView inventory name; domain suffixes are stripped.
- **Unique names enforced** — duplicate server or chassis names (after domain-stripping) are skipped with a warning.
- **Chassis skip** — enclosures without a URI and blade servers whose chassis wasn't synced are skipped automatically.
- **Field-level diffs** — dry-run shows exactly which fields would change, with ANSI colour output in a TTY.
