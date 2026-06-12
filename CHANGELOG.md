# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.1.2] - 2026-06-12

### Fixed
- DeviceTypes for enclosures now created with `subdevice_role=parent` and blades with `subdevice_role=child`, resolving the "this type of device does not support device bays" error from NetBox

## [1.1.1] - 2026-06-12

### Changed
- Device bays are now named `Device Bay N` instead of `Bay N`

## [1.1.0] - 2026-06-12

### Added
- `oneview_to_netbox.py` — sync HP OneView enclosures and server hardware into NetBox as Device records, with blade-to-chassis DeviceBay linking
- `--tenant` option: assign all synced devices to a NetBox tenant (resolved by name or slug)
- `--device-types-file` option: YAML file mapping OneView model names to NetBox DeviceType definitions (model name, u_height) via case-insensitive substring matching
- `hpe_device_types.yaml` — bundled example with 25 common HPE enclosure, blade, and rack server entries
- Field-level dry-run diffs with ANSI colour output for chassis and server sync
- `--delete-missing` to remove NetBox devices (matching role + site) absent from OneView

### Changed
- Manufacturer name for HPE devices set to `HPE` (previously `Hewlett Packard Enterprise`)

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
