# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.0.0] - 2026-06-01

### Added
- `nmap_to_netbox.py` — parse nmap XML and upsert IP address records into NetBox
- `scan_tagged_prefixes.py` — orchestrate nmap scans across all NetBox-tagged prefixes
- Protected status logic: `reserved`, `dhcp`, and `slaac` IPs are never overwritten
- Field-level dry-run diffs with ANSI colour output
- Prefix head/tail reservation (`--reserve-head`, `--reserve-tail`)
- Auto-creation of scan tag in NetBox if missing
- Live reverse DNS lookup support (`--live-rdns`)

### Changed
- `--dry-run` in `scan_tagged_prefixes.py` now runs the real nmap scan and diffs results
  against NetBox without writing, instead of stubbing nmap with empty XML
