# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.5.1] - 2026-06-24

### Changed
- README updated to document rack sync, `--skip-racks`, `--rack-filter`, rack device positioning, sync order, and verbose rack output

## [1.5.0] - 2026-06-24

### Added
- Rack sync: OneView racks are created/updated in NetBox (`dcim.racks`) with name, serial, U-height, site, and tenant resolved the same way as devices
- Rack device positions: after devices are synced a second pass places each device into its rack slot using OneView `rackMounts` data; `topUSlot` is converted to NetBox bottom-U position
- `--skip-racks` flag to bypass rack sync
- `--rack-filter NAME …` to limit rack sync to racks whose name contains one of the given strings (case-insensitive)
- Server hardware URIs are now tracked internally so rack position assignment works for both rack servers and enclosures

## [1.4.1] - 2026-06-24

### Changed
- README updated to reflect label resolution being on by default; usage examples simplified, options table updated to `--no-label-site` / `--no-label-tenant`, debug labels section removed

## [1.4.0] - 2026-06-24

### Changed
- Label-based site and tenant resolution is now **on by default**; use `--no-label-site` / `--no-label-tenant` to disable
- Removed `[DEBUG-LABELS]` verbose diagnostic output now that label fetch is confirmed working

## [1.3.9] - 2026-06-24

### Added
- `--verbose` now activates label fetch diagnostics: for each device it prints the URL attempted, HTTP status code, and (when empty or failed) the raw response body and keys — allows identifying the correct OneView labels endpoint and response format without guessing

## [1.3.8] - 2026-06-24

### Changed
- README updated to document `--verbose` flag in the options table and added a "Verbose output" section with annotated example output

## [1.3.7] - 2026-06-24

### Added
- `--verbose` flag: prints a per-device detail block showing OneView source values (model, serial, power state, blade position) and the resolved NetBox values (site and tenant with source — label name or default — and status); unresolved site/tenant is noted before the skip warning

## [1.3.6] - 2026-06-24

### Changed
- README debugging section updated to note that both OneView label endpoint forms are tried automatically; clarified that a `[LABELS]` line with no match means a NetBox name/slug mismatch, not a fetch error

## [1.3.5] - 2026-06-24

### Fixed
- Labels were never returned because `requests` percent-encodes query parameters — `/rest/server-hardware/UUID` became `%2Frest%2Fserver-hardware%2FUUID` which OneView did not recognise; query param is now embedded directly in the URL string to preserve literal slashes
- Path-based endpoint form (`/rest/labels/resources//rest/server-hardware/UUID`) added as a fallback for OneView versions that do not support the query-param form

## [1.3.4] - 2026-06-24

### Changed
- README updated to document label debugging output and how to diagnose label fetch and matching issues

## [1.3.3] - 2026-06-24

### Fixed
- Label fetch was silently returning empty results because the OneView `/rest/labels/resources` response uses `{"labels": [...]}` not `{"members": [...]}` as previously assumed; both keys are now tried for compatibility
- Label API errors are now surfaced as warnings on stderr instead of being swallowed silently

### Added
- `[LABELS]` log line printed per device when labels are fetched, showing the label names resolved from OneView — aids debugging of label-to-site/tenant matching

## [1.3.2] - 2026-06-24

### Changed
- README key options table split into Required and Optional sections; required args (`--oneview-host`, `--oneview-user`, `--netbox-url`, `--token`) now documented
- `--label-site` / `--label-tenant` descriptions updated to reflect they are the primary resolution mechanism, not just an override

## [1.3.1] - 2026-06-24

### Changed
- README updated to document resolution order (label → global default → skip), clarify --site/--tenant as optional fallbacks that must already exist, and show example warning message format

## [1.3.0] - 2026-06-24

### Changed
- `--site` and `--tenant` are now optional; if omitted, site and tenant must be resolved per device via `--label-site` / `--label-tenant`
- `_require_site` and `_resolve_tenant` no longer auto-create entries; they exit with a clear error if an explicitly provided value is not found in NetBox
- Startup status line shows `(from labels)` when no global site or tenant default is configured

### Fixed
- "Unknown" site and tenant entries are no longer silently created in NetBox when values are unresolved
- Skip warning now names the specific missing field(s): e.g. `site not set` or `site, tenant not set`

## [1.2.4] - 2026-06-24

### Changed
- README updated to document the constraint-safe two-step device lookup behaviour

## [1.2.3] - 2026-06-24

### Fixed
- 400 error (`dcim_device_unique_name_site_tenant` constraint violation) when site or tenant changed; device lookup now tries an exact (name, site, tenant) match first, only falling back to name-only search to locate a device to move — preventing collisions with an already-existing record at the target location

## [1.2.2] - 2026-06-24

### Changed
- README usage examples updated to show `--site` as optional and demonstrate label-only vs explicit fallback patterns
- Fixed duplicate phrase in Behaviour section
- Intro bullet updated to mention skip behaviour for unresolved site/tenant

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
