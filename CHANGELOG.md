# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.2.1] - 2026-06-24

### Changed
- README updated to document in-place site/tenant updates, unresolved field skipping behaviour, and clarified label-based mapping section

## [1.2.0] - 2026-06-24

### Fixed
- Devices whose site or tenant changed via label are now updated in place instead of creating a duplicate record at the new site/tenant
- Site changes are now included in dry-run field-level diffs

### Changed
- Devices are skipped if **either** site or tenant is unresolved (Unknown), not just when both are; the warning message names the specific missing field(s)

## [1.1.9] - 2026-06-24

### Changed
- README updated to reflect Unknown default site/tenant, label format reference table, and unique name enforcement behaviour

## [1.1.8] - 2026-06-24

### Added
- Duplicate server and chassis names are now detected and skipped with a warning rather than creating conflicting NetBox records

### Changed
- `--site` and `--tenant` now default to `Unknown` and are no longer required; both are auto-created in NetBox if they don't already exist
- Site and tenant are always assigned to every synced device; OneView labels override the defaults per device

## [1.1.7] - 2026-06-24

### Added
- `--chassis-filter NAME [NAME ...]` and `--server-filter NAME [NAME ...]` to sync only devices whose names contain one of the given strings (case-insensitive); useful for updating a single server or chassis without processing everything

## [1.1.6] - 2026-06-24

### Changed
- `--label-site` and `--label-tenant` are now boolean flags instead of prefix strings; each device label is matched directly against NetBox site/tenant names and slugs (required by OneView's alphanumeric-only label constraint)

## [1.1.5] - 2026-06-24

### Added
- `--label-site PREFIX` and `--label-tenant PREFIX` CLI args to override site/tenant per device based on OneView labels (e.g. label `site:DC-West` with `--label-site "site:"` sets that device's site to `DC-West`)
- Labels are fetched from the OneView `/rest/labels/resources` endpoint only when a label-mapping arg is supplied; unknown values warn and fall back to the global defaults

## [1.1.4] - 2026-06-24

### Changed
- Blade servers whose `locationUri` does not match a synced chassis are now skipped instead of being created without a DeviceBay link

## [1.1.3] - 2026-06-24

### Added
- `--oneview-password` is now optional; the script prompts securely via `getpass` when omitted

### Changed
- Server names now use the `serverName` field (OS hostname) instead of the OneView inventory name
- Server names have any domain suffix stripped, keeping only the short hostname
- Chassis/enclosures with no URI defined in OneView are skipped instead of partially processed

## [1.1.2] - 2026-06-12

### Fixed
- DeviceTypes for enclosures now created with `subdevice_role=parent` and blades with `subdevice_role=child`, resolving the "this type of device does not support device bays" error from NetBox
- `subdevice_role` is now patched in-place on existing DeviceTypes that were created without it, so the fix applies even when the DeviceType already exists in NetBox

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
