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
| `--site` | required | NetBox site where devices are placed |
| `--tenant` | — | NetBox tenant assigned to all synced devices |
| `--chassis-role` | `Chassis` | DeviceRole name for enclosures |
| `--server-role` | `Server` | DeviceRole name for servers |
| `--device-types-file` | — | YAML file mapping OneView model names to NetBox DeviceType definitions |
| `--label-site PREFIX` | — | OneView label prefix that sets the site per device (e.g. `site:`) |
| `--label-tenant PREFIX` | — | OneView label prefix that sets the tenant per device (e.g. `tenant:`) |
| `--skip-chassis` | off | Skip enclosure sync |
| `--skip-servers` | off | Skip server hardware sync |
| `--delete-missing` | off | Delete NetBox devices absent from OneView (use `--dry-run` first) |
| `--no-ssl-verify` | off | Disable TLS verification (lab use) |
| `--dry-run` | off | Show field-level diffs without writing to NetBox |

### Label-based site/tenant mapping

OneView labels can override the global `--site` and `--tenant` on a per-device basis. Labels follow a `prefix:value` convention:

```bash
# Labels "site:DC-West" and "tenant:ACME" on a device will route it
# to site=DC-West and tenant=ACME instead of the global defaults
python oneview_to_netbox.py ... \
    --site          fallback-dc \
    --label-site    "site:" \
    --label-tenant  "tenant:"
```

If a label value doesn't match a known NetBox site or tenant, a warning is printed and the global default is used.

### Behaviour

- **Idempotent** — safe to run repeatedly; unchanged records are skipped.
- **Server names** — uses the OS hostname (`serverName`) in preference to the OneView inventory name; domain suffixes are stripped.
- **Chassis skip** — enclosures without a URI and blade servers whose chassis wasn't synced are skipped automatically.
- **Field-level diffs** — dry-run shows exactly which fields would change, with ANSI colour output in a TTY.
