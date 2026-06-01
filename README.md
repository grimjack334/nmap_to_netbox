# nmap_to_netbox

Synchronize nmap scan results into [NetBox](https://netbox.dev) IPAM. Discovers live hosts, resolves reverse DNS, and upserts IP address records — without clobbering reserved or manually-managed entries.

## Tools

| Script | Purpose |
|---|---|
| `nmap_to_netbox.py` | Parse a single nmap XML file and import results into NetBox |
| `scan_tagged_prefixes.py` | Orchestrate scanning of all NetBox prefixes carrying a given tag |

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
