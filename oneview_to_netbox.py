#!/usr/bin/env python3
"""
oneview_to_netbox.py
--------------------
Sync HP OneView server hardware and enclosures into NetBox as Devices.

What it does:
  - Reads Enclosures from OneView → creates/updates NetBox Device records
    (role=chassis) and creates DeviceBays for each blade slot
  - Reads Server Hardware from OneView → creates/updates NetBox Device records
    (role=server), linking blade servers to their parent chassis DeviceBay
  - Auto-creates Manufacturers, DeviceTypes, and DeviceRoles as needed
  - Dry-run mode shows field-level diffs without writing anything

Requirements:
    pip install pynetbox requests

Usage:
    python oneview_to_netbox.py \\
        --oneview-host  https://oneview.example.com \\
        --oneview-user  Administrator \\
        --oneview-password SECRET \\
        --netbox-url    https://netbox.example.com \\
        --token         YOUR_API_TOKEN \\
        --site          my-datacenter \\
        [--oneview-api-version 2400] \\
        [--chassis-role Chassis] \\
        [--server-role  Server] \\
        [--tenant       my-tenant] \\
        [--no-ssl-verify] \\
        [--skip-chassis] \\
        [--skip-servers] \\
        [--dry-run]
"""

import argparse
import getpass
import re
import sys
from typing import Optional

import requests
import urllib3

try:
    import pynetbox
except ImportError:
    sys.exit("Missing dependency: pip install pynetbox requests")


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# ANSI colours (disabled when not a TTY)
# ---------------------------------------------------------------------------

def _colour(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


red    = lambda t: _colour("31", t)
green  = lambda t: _colour("32", t)
yellow = lambda t: _colour("33", t)
cyan   = lambda t: _colour("36", t)
bold   = lambda t: _colour("1",  t)


# ---------------------------------------------------------------------------
# Dry-run stub
# ---------------------------------------------------------------------------

class _Stub:
    """Lightweight placeholder returned in dry-run mode instead of real NB objects."""
    id = None

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# HP OneView REST client
# ---------------------------------------------------------------------------

class OneViewClient:
    """Minimal HP OneView REST client using session-token authentication."""

    DEFAULT_API_VERSION = 2400

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        api_version: Optional[int] = None,
        verify_ssl: bool = False,
    ):
        self.base_url   = host.rstrip("/")
        self.verify_ssl = verify_ssl
        self.session    = requests.Session()
        self.session.verify = verify_ssl

        if api_version is None:
            api_version = self._detect_api_version()
        self.api_version = api_version

        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "X-API-Version": str(self.api_version),
        })
        self._login(username, password)

    # ---- setup -------------------------------------------------------------

    def _detect_api_version(self) -> int:
        try:
            resp = requests.get(
                f"{self.base_url}/rest/version",
                verify=self.verify_ssl,
                headers={"Accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("currentVersion", self.DEFAULT_API_VERSION)
        except Exception:
            return self.DEFAULT_API_VERSION

    def _login(self, username: str, password: str) -> None:
        resp = self.session.post(
            f"{self.base_url}/rest/login-sessions",
            json={"userName": username, "password": password},
            timeout=30,
        )
        resp.raise_for_status()
        self.session.headers["Auth"] = resp.json()["sessionID"]

    # ---- data retrieval ----------------------------------------------------

    def _get_all(self, endpoint: str) -> list:
        results = []
        start   = 0
        count   = 100
        while True:
            resp = self.session.get(
                f"{self.base_url}{endpoint}",
                params={"start": start, "count": count},
                timeout=60,
            )
            resp.raise_for_status()
            data    = resp.json()
            members = data.get("members", [])
            results.extend(members)
            if len(results) >= data.get("total", 0):
                break
            start += len(members)
        return results

    def get_enclosures(self) -> list:
        return self._get_all("/rest/enclosures")

    def get_server_hardware(self) -> list:
        return self._get_all("/rest/server-hardware")

    def get_resource_labels(self, resource_uri: str) -> list:
        """Return label names assigned to a resource URI."""
        if not resource_uri:
            return []
        try:
            resp = self.session.get(
                f"{self.base_url}/rest/labels/resources",
                params={"resourceUri": resource_uri},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            # OneView returns {"labels": [...]}; fall back to "members" for older API versions
            items = data.get("labels") or data.get("members") or []
            return [m.get("name", "") for m in items if m.get("name")]
        except Exception as exc:
            print(f"  {yellow('[WARN]')} Could not fetch labels for {resource_uri}: {exc}",
                  file=sys.stderr)
            return []

    def logout(self) -> None:
        try:
            self.session.delete(f"{self.base_url}/rest/login-sessions", timeout=10)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# NetBox syncer
# ---------------------------------------------------------------------------

class NetBoxSync:

    MANUFACTURER_NAME = "HPE"
    MANUFACTURER_SLUG = "hpe"

    def __init__(
        self,
        url: str,
        token: str,
        site_name: Optional[str] = None,
        chassis_role: str = "Chassis",
        server_role: str = "Server",
        tenant_name: Optional[str] = None,
        device_types_file: Optional[str] = None,
        label_site: bool = False,
        label_tenant: bool = False,
        dry_run: bool = False,
    ):
        self.dry_run          = dry_run
        self.chassis_role     = chassis_role
        self.server_role      = server_role
        self.nb               = pynetbox.api(url, token=token)
        self._label_site      = label_site
        self._label_tenant    = label_tenant

        self._manufacturer                  = None
        self._device_types: dict            = {}   # slug → object
        self._device_roles: dict            = {}   # slug → object
        self._enclosure_uri_map: dict       = {}   # OV uri → nb device
        self._seen_chassis_names: set       = set()
        self._seen_server_names: set        = set()
        self._site_cache: dict              = {}   # name → site object
        self._tenant_cache: dict            = {}   # name → tenant object
        self._type_defs: list               = self._load_type_defs(device_types_file) if device_types_file else []

        try:
            self.nb.status()
        except Exception as exc:
            sys.exit(f"Cannot reach NetBox: {exc}")

        self._site   = self._require_site(site_name)   if site_name   else None
        self._tenant = self._resolve_tenant(tenant_name) if tenant_name else None

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _slugify(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50]

    def _require_site(self, name: str):
        for filt in ({"name": name}, {"slug": self._slugify(name)}):
            sites = list(self.nb.dcim.sites.filter(**filt))
            if sites:
                return sites[0]
        sys.exit(f"Site '{name}' not found in NetBox. Create it first.")

    def _resolve_tenant(self, name: str):
        for filt in ({"name": name}, {"slug": self._slugify(name)}):
            tenants = list(self.nb.tenancy.tenants.filter(**filt))
            if tenants:
                return tenants[0]
        sys.exit(f"Tenant '{name}' not found in NetBox. Create it first.")

    def _find_site(self, name: str):
        """Look up a site by name/slug; return None silently if not found."""
        if name in self._site_cache:
            return self._site_cache[name]
        for filt in ({"name": name}, {"slug": self._slugify(name)}):
            sites = list(self.nb.dcim.sites.filter(**filt))
            if sites:
                self._site_cache[name] = sites[0]
                return sites[0]
        self._site_cache[name] = None
        return None

    def _find_tenant(self, name: str):
        """Look up a tenant by name/slug; return None silently if not found."""
        if name in self._tenant_cache:
            return self._tenant_cache[name]
        for filt in ({"name": name}, {"slug": self._slugify(name)}):
            tenants = list(self.nb.tenancy.tenants.filter(**filt))
            if tenants:
                self._tenant_cache[name] = tenants[0]
                return tenants[0]
        self._tenant_cache[name] = None
        return None

    def _apply_labels(self, labels: list) -> tuple:
        """Return (site_override, tenant_override) by matching labels against NetBox sites/tenants."""
        site_override   = None
        tenant_override = None
        for label in labels:
            if self._label_site and site_override is None:
                site_override = self._find_site(label)
            if self._label_tenant and tenant_override is None:
                tenant_override = self._find_tenant(label)
            if site_override and tenant_override:
                break
        return site_override, tenant_override

    @staticmethod
    def _load_type_defs(path: str) -> list:
        try:
            import yaml
        except ImportError:
            sys.exit("Missing dependency for --device-types-file: pip install pyyaml")
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict) or "device_types" not in data:
                sys.exit(f"Device types file '{path}' must contain a top-level 'device_types' list.")
            return data["device_types"]
        except OSError as exc:
            sys.exit(f"Cannot read device types file '{path}': {exc}")
        except Exception as exc:
            sys.exit(f"Invalid device types file '{path}': {exc}")

    def _resolve_type_def(self, ov_model: str) -> Optional[dict]:
        """Return the first type definition whose 'match' is a substring of ov_model."""
        lower = ov_model.lower()
        for defn in self._type_defs:
            if defn.get("match", "").lower() in lower:
                return defn
        return None

    def _get_manufacturer(self):
        if self._manufacturer:
            return self._manufacturer
        existing = list(self.nb.dcim.manufacturers.filter(slug=self.MANUFACTURER_SLUG))
        if existing:
            self._manufacturer = existing[0]
            return self._manufacturer
        if self.dry_run:
            self._manufacturer = _Stub(name=self.MANUFACTURER_NAME, slug=self.MANUFACTURER_SLUG)
            print(f"  {yellow('[DRY-RUN]')} would create Manufacturer: {self.MANUFACTURER_NAME}")
            return self._manufacturer
        self._manufacturer = self.nb.dcim.manufacturers.create(
            name=self.MANUFACTURER_NAME,
            slug=self.MANUFACTURER_SLUG,
        )
        print(f"  {green('[CREATED]')} Manufacturer: {self.MANUFACTURER_NAME}")
        return self._manufacturer

    def _get_device_type(self, ov_model: str, u_height: int = 1, subdevice_role: Optional[str] = None):
        defn = self._resolve_type_def(ov_model)
        model          = defn["model"]          if defn and "model"          in defn else ov_model
        u_height       = defn["u_height"]       if defn and "u_height"       in defn else u_height
        subdevice_role = defn["subdevice_role"] if defn and "subdevice_role" in defn else subdevice_role

        slug = self._slugify(model)
        if slug in self._device_types:
            return self._device_types[slug]
        existing = list(self.nb.dcim.device_types.filter(slug=slug))
        if existing:
            dt = existing[0]
            cur_role = dt.subdevice_role.value if dt.subdevice_role else None
            if subdevice_role and cur_role != subdevice_role:
                if not self.dry_run:
                    dt.update({"subdevice_role": subdevice_role})
                    print(f"  {yellow('[UPDATED]')} DeviceType {model}: subdevice_role → {subdevice_role}")
            self._device_types[slug] = dt
            return dt
        if self.dry_run:
            stub = _Stub(model=model, slug=slug)
            self._device_types[slug] = stub
            print(f"  {yellow('[DRY-RUN]')} would create DeviceType: {model}")
            return stub
        mfr = self._get_manufacturer()
        create_kwargs = dict(
            manufacturer=mfr.id,
            model=model,
            slug=slug,
            u_height=u_height,
        )
        if subdevice_role:
            create_kwargs["subdevice_role"] = subdevice_role
        dt = self.nb.dcim.device_types.create(**create_kwargs)
        self._device_types[slug] = dt
        print(f"  {green('[CREATED]')} DeviceType: {model}")
        return dt

    def _get_device_role(self, role_name: str):
        slug = self._slugify(role_name)
        if slug in self._device_roles:
            return self._device_roles[slug]
        existing = list(self.nb.dcim.device_roles.filter(slug=slug))
        if existing:
            self._device_roles[slug] = existing[0]
            return existing[0]
        if self.dry_run:
            stub = _Stub(name=role_name, slug=slug)
            self._device_roles[slug] = stub
            print(f"  {yellow('[DRY-RUN]')} would create DeviceRole: {role_name}")
            return stub
        role = self.nb.dcim.device_roles.create(
            name=role_name,
            slug=slug,
            color="2196f3",
        )
        self._device_roles[slug] = role
        print(f"  {green('[CREATED]')} DeviceRole: {role_name}")
        return role

    # ---- device lookup -----------------------------------------------------

    def _find_device(self, name: str, site_id, tenant_id) -> list:
        """Return matching device(s), preferring exact (name, site, tenant) over name-only.

        Searching by name only can return a device at a different location; updating it
        to the new (site, tenant) then hits the unique constraint if a device already
        exists there.  By trying the exact target first we avoid that collision.
        """
        if site_id is not None and tenant_id is not None:
            exact = list(self.nb.dcim.devices.filter(
                name=name, site_id=site_id, tenant_id=tenant_id,
            ))
            if exact:
                return exact
        return list(self.nb.dcim.devices.filter(name=name))

    # ---- diff --------------------------------------------------------------

    def _diff_device(self, existing, desired: dict) -> list:
        changes = []

        cur_serial = existing.serial or ""
        new_serial = desired.get("serial", "")
        if new_serial and cur_serial != new_serial:
            changes.append(f"    serial: {red(repr(cur_serial))} → {green(repr(new_serial))}")

        try:
            cur_status = existing.status.value
        except AttributeError:
            cur_status = str(existing.status)
        new_status = desired.get("status", "")
        if new_status and cur_status != new_status:
            changes.append(f"    status: {red(cur_status)} → {green(new_status)}")

        new_site_id = desired.get("site")
        cur_site_id = existing.site.id if existing.site else None
        if new_site_id != cur_site_id:
            cur_label = str(existing.site) if existing.site else "None"
            new_label = str(new_site_id) if new_site_id else "None"
            changes.append(f"    site: {red(cur_label)} → {green(new_label)}")

        new_tenant_id = desired.get("tenant")
        cur_tenant_id = existing.tenant.id if existing.tenant else None
        if new_tenant_id != cur_tenant_id:
            cur_label = str(existing.tenant) if existing.tenant else "None"
            new_label = str(new_tenant_id) if new_tenant_id else "None"
            changes.append(f"    tenant: {red(cur_label)} → {green(new_label)}")

        return changes

    # ---- enclosure sync ----------------------------------------------------

    def sync_enclosure(self, enc: dict, labels: list = None) -> tuple:
        """Create or update a chassis device from a OneView enclosure."""
        name   = (enc.get("name") or "").strip()
        serial = enc.get("serialNumber") or ""
        model  = (
            enc.get("enclosureModel")
            or (enc.get("enclosureType") or {}).get("enclosureModel")
            or "HPE BladeSystem"
        )
        bay_count = enc.get("deviceBayCount") or 0
        uri       = enc.get("uri") or ""

        if not name:
            return "skipped", None
        if not uri:
            return "skipped", None

        if name in self._seen_chassis_names:
            print(f"  {yellow('[WARN]')} Duplicate chassis name '{name}' — skipping")
            return "skipped", None

        site_override, tenant_override = self._apply_labels(labels or [])
        site   = site_override   or self._site
        tenant = tenant_override or self._tenant

        missing = [f for f, v in (("site", site), ("tenant", tenant)) if v is None]
        if missing:
            print(f"  {yellow('[WARN]')} Chassis '{name}' — {', '.join(missing)} not set — skipping")
            return "skipped", None

        self._seen_chassis_names.add(name)
        dt   = self._get_device_type(model, u_height=10, subdevice_role="parent")
        role = self._get_device_role(self.chassis_role)

        payload = {
            "name":        name,
            "device_type": dt.id,
            "role":        role.id,
            "site":        site.id,
            "serial":      serial,
            "status":      "active",
            "tenant":      tenant.id,
        }

        existing = self._find_device(name, site.id, tenant.id)

        if self.dry_run:
            if existing:
                changes = self._diff_device(existing[0], payload)
                if changes:
                    print(f"  {yellow('[DRY-RUN UPDATE]')} Chassis: {bold(name)}")
                    for c in changes:
                        print(c)
                    return "updated", None
                print(f"  {cyan('[DRY-RUN NO CHANGE]')} Chassis: {name}")
                return "unchanged", None
            print(
                f"  {green('[DRY-RUN CREATE]')} Chassis: {bold(name)}"
                f"  model={model}  serial={serial}  bays={bay_count}"
            )
            return "created", None

        if existing:
            device = existing[0]
            changes = self._diff_device(device, payload)
            if changes:
                device.update(payload)
                action = "updated"
            else:
                action = "unchanged"
        else:
            device = self.nb.dcim.devices.create(**payload)
            action = "created"

        if uri:
            self._enclosure_uri_map[uri] = device

        self._sync_device_bays(device, bay_count)
        return action, device

    def _sync_device_bays(self, device, bay_count: int) -> None:
        """Ensure the chassis has device bays numbered Device Bay 1 … Device Bay N."""
        if not bay_count:
            return
        existing_bays = {
            b.name for b in self.nb.dcim.device_bays.filter(device_id=device.id)
        }
        created = 0
        for i in range(1, bay_count + 1):
            bay_name = f"Device Bay {i}"
            if bay_name not in existing_bays:
                self.nb.dcim.device_bays.create(device=device.id, name=bay_name)
                created += 1
        if created:
            print(f"    {green('[BAYS]')} Created {created} device bay(s) on {device.name}")

    # ---- server sync -------------------------------------------------------

    def sync_server(self, server: dict, labels: list = None) -> tuple:
        """Create or update a server device from OneView server hardware."""
        raw_name     = (server.get("serverName") or server.get("name") or "").strip()
        name         = raw_name.split(".")[0]
        serial       = server.get("serialNumber") or ""
        model        = server.get("model") or server.get("shortModel") or "HPE Server"
        power_state  = server.get("powerState") or "Unknown"
        location_uri = server.get("locationUri") or ""
        position     = server.get("position")   # int bay number (None for rack servers)

        if not name:
            return "skipped", None

        if name in self._seen_server_names:
            print(f"  {yellow('[WARN]')} Duplicate server name '{name}' — skipping")
            return "skipped", None
        self._seen_server_names.add(name)
        is_blade  = bool(location_uri and position is not None)
        if is_blade and location_uri not in self._enclosure_uri_map:
            print(f"  {yellow('[SKIPPED]')} Server: {name}  — chassis URI not found ({location_uri})")
            return "skipped", None

        site_override, tenant_override = self._apply_labels(labels or [])
        site   = site_override   or self._site
        tenant = tenant_override or self._tenant

        missing = [f for f, v in (("site", site), ("tenant", tenant)) if v is None]
        if missing:
            print(f"  {yellow('[WARN]')} Server '{name}' — {', '.join(missing)} not set — skipping")
            return "skipped", None

        nb_status = "active" if power_state == "On" else "offline"
        dt        = self._get_device_type(model, u_height=0 if is_blade else 1,
                                           subdevice_role="child" if is_blade else None)
        role      = self._get_device_role(self.server_role)

        payload = {
            "name":        name,
            "device_type": dt.id,
            "role":        role.id,
            "site":        site.id,
            "serial":      serial,
            "status":      nb_status,
            "tenant":      tenant.id,
        }

        existing = self._find_device(name, site.id, tenant.id)

        blade_str = f"  bay={position}" if is_blade else ""

        if self.dry_run:
            if existing:
                changes = self._diff_device(existing[0], payload)
                if changes:
                    print(f"  {yellow('[DRY-RUN UPDATE]')} Server: {bold(name)}{blade_str}")
                    for c in changes:
                        print(c)
                    return "updated", None
                print(f"  {cyan('[DRY-RUN NO CHANGE]')} Server: {name}{blade_str}")
                return "unchanged", None
            print(
                f"  {green('[DRY-RUN CREATE]')} Server: {bold(name)}"
                f"  model={model}  serial={serial}  status={nb_status}{blade_str}"
            )
            return "created", None

        if existing:
            device = existing[0]
            changes = self._diff_device(device, payload)
            if changes:
                device.update(payload)
                action = "updated"
            else:
                action = "unchanged"
        else:
            device = self.nb.dcim.devices.create(**payload)
            action = "created"

        if is_blade:
            self._link_blade_to_bay(device, location_uri, int(position))

        return action, device

    def _link_blade_to_bay(
        self, blade, enclosure_uri: str, position: int
    ) -> None:
        """Install a blade device into its chassis DeviceBay."""
        chassis = self._enclosure_uri_map.get(enclosure_uri)
        if chassis is None:
            print(
                f"    {yellow('[WARN]')} Cannot link {blade.name} to bay {position}"
                f" — chassis not synced in this run"
            )
            return

        bay_name = f"Device Bay {position}"
        bays = list(self.nb.dcim.device_bays.filter(device_id=chassis.id, name=bay_name))
        if not bays:
            print(f"    {yellow('[WARN]')} '{bay_name}' not found on {chassis.name}")
            return

        bay = bays[0]
        if bay.installed_device is None or bay.installed_device.id != blade.id:
            bay.update({"installed_device": blade.id})
            print(f"    {green('[BAY LINKED]')} {blade.name} → {chassis.name} {bay_name}")

    # ---- delete missing ----------------------------------------------------

    def delete_missing(self, role_name: str, seen_names: set) -> dict:
        """Delete NetBox devices (role=role_name, site=this site) not in seen_names."""
        slug = self._slugify(role_name)
        role_obj = self._device_roles.get(slug)
        if role_obj is None:
            existing = list(self.nb.dcim.device_roles.filter(slug=slug))
            if not existing:
                return {"deleted": 0, "errors": 0}
            role_obj = existing[0]

        nb_devices = list(self.nb.dcim.devices.filter(
            site_id=self._site.id,
            role_id=role_obj.id,
        ))

        stats = {"deleted": 0, "errors": 0}
        for device in nb_devices:
            if device.name in seen_names:
                continue
            serial_str = f"  serial={device.serial}" if device.serial else ""
            if self.dry_run:
                print(f"  {yellow('[DRY-RUN DELETE]')} {device.name}{serial_str}")
                stats["deleted"] += 1
                continue
            try:
                name_str = device.name
                device.delete()
                print(f"  {red('[DELETED]')} {name_str}{serial_str}")
                stats["deleted"] += 1
            except Exception as exc:
                stats["errors"] += 1
                print(f"  {red('[ERROR]')} delete {device.name}: {exc}", file=sys.stderr)

        return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sync HP OneView servers and enclosures into NetBox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    g = p.add_argument_group("HP OneView")
    g.add_argument("--oneview-host",           required=True,
                   help="OneView appliance URL (e.g. https://oneview.example.com)")
    g.add_argument("--oneview-user",           required=True, help="OneView username")
    g.add_argument("--oneview-password",       default=None,
                   help="OneView password (prompted if omitted)")
    g.add_argument("--oneview-api-version",    type=int, default=None,
                   help="OneView REST API version (default: auto-detect)")

    g = p.add_argument_group("NetBox")
    g.add_argument("--netbox-url", required=True, help="NetBox base URL (no trailing slash)")
    g.add_argument("--token",      required=True, help="NetBox API token")
    g.add_argument("--site",   default=None,
                   help="NetBox site name for devices (must already exist); if omitted, "
                        "site must be resolved per device via --label-site")
    g.add_argument("--tenant", default=None,
                   help="NetBox tenant name for devices (must already exist); if omitted, "
                        "tenant must be resolved per device via --label-tenant")

    g = p.add_argument_group("Device types")
    g.add_argument("--device-types-file", default=None, metavar="FILE",
                   help="YAML file mapping OneView model names to NetBox DeviceType definitions "
                        "(model name, u_height). See hpe_device_types.yaml for an example.")

    g = p.add_argument_group("Device roles")
    g.add_argument("--chassis-role", default="Chassis",
                   help="DeviceRole name for enclosures (default: Chassis)")
    g.add_argument("--server-role",  default="Server",
                   help="DeviceRole name for servers (default: Server)")

    g = p.add_argument_group("Label mappings")
    g.add_argument("--label-site",   action="store_true",
                   help="Match device labels against NetBox site names/slugs to override "
                        "the default site per device")
    g.add_argument("--label-tenant", action="store_true",
                   help="Match device labels against NetBox tenant names/slugs to override "
                        "the default tenant per device")

    g = p.add_argument_group("Behaviour")
    g.add_argument("--no-ssl-verify", action="store_true",
                   help="Disable TLS certificate verification for OneView (lab use)")
    g.add_argument("--skip-chassis",  action="store_true",
                   help="Skip enclosure/chassis sync")
    g.add_argument("--skip-servers",  action="store_true",
                   help="Skip server hardware sync")
    g.add_argument("--chassis-filter", nargs="+", metavar="NAME", default=None,
                   help="Only sync chassis whose name contains one of these strings "
                        "(case-insensitive); all others are skipped")
    g.add_argument("--server-filter",  nargs="+", metavar="NAME", default=None,
                   help="Only sync servers whose name contains one of these strings "
                        "(case-insensitive); all others are skipped")
    g.add_argument("--delete-missing", action="store_true",
                   help="Delete NetBox devices (matching role+site) absent from OneView. "
                        "Only applies to phases that were not skipped. "
                        "Use --dry-run first to preview.")
    g.add_argument("--dry-run",       action="store_true",
                   help="Preview all changes with field-level diffs; nothing is written")
    return p


def _name_matches(name: str, filters: list) -> bool:
    """Return True if name contains any filter string (case-insensitive)."""
    lower = name.lower()
    return any(f.lower() in lower for f in filters)


def _print_stats(label: str, stats: dict) -> None:
    print(
        f"\n{label} summary — "
        f"Created: {stats['created']}  "
        f"Updated: {stats['updated']}  "
        f"Unchanged: {stats['unchanged']}  "
        f"Skipped: {stats['skipped']}  "
        f"Errors: {stats['errors']}"
    )


def _print_delete_stats(label: str, stats: dict) -> None:
    print(
        f"\n{label} deletion summary — "
        f"Deleted: {stats['deleted']}  "
        f"Errors: {stats['errors']}"
    )


def main() -> None:
    args = build_parser().parse_args()

    if args.dry_run:
        print(bold("=== DRY RUN — no changes will be written ===\n"))

    password = args.oneview_password or getpass.getpass("OneView password: ")

    # Connect to OneView
    print("Connecting to HP OneView …")
    try:
        ov = OneViewClient(
            host=args.oneview_host,
            username=args.oneview_user,
            password=password,
            api_version=args.oneview_api_version,
            verify_ssl=not args.no_ssl_verify,
        )
    except Exception as exc:
        sys.exit(f"OneView login failed: {exc}")
    print(f"  Connected  (API version {ov.api_version})")

    # Connect to NetBox
    print("\nConnecting to NetBox …")
    syncer = NetBoxSync(
        url=args.netbox_url,
        token=args.token,
        site_name=args.site,
        chassis_role=args.chassis_role,
        server_role=args.server_role,
        tenant_name=args.tenant,
        device_types_file=args.device_types_file,
        label_site=args.label_site,
        label_tenant=args.label_tenant,
        dry_run=args.dry_run,
    )
    use_labels = args.label_site or args.label_tenant
    site_str   = syncer._site.name   if syncer._site   else "(from labels)"
    tenant_str = syncer._tenant.name if syncer._tenant else "(from labels)"
    print(f"  Connected  (site: {site_str}  tenant: {tenant_str})")

    # ---- Enclosures ---------------------------------------------------------
    if not args.skip_chassis:
        print("\nFetching enclosures from OneView …")
        enclosures = ov.get_enclosures()
        if args.chassis_filter:
            enclosures = [e for e in enclosures
                          if _name_matches(e.get("name") or "", args.chassis_filter)]
        print(f"  Found {len(enclosures)} enclosure(s)\n")

        stats: dict = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        for enc in enclosures:
            try:
                labels = ov.get_resource_labels(enc.get("uri", "")) if use_labels else []
                if use_labels and labels:
                    print(f"  {cyan('[LABELS]')} {enc.get('name', '?')}: {', '.join(labels)}")
                action, device = syncer.sync_enclosure(enc, labels=labels)
                stats[action] += 1
                if not args.dry_run and action != "skipped":
                    colour = green if action == "created" else (yellow if action == "updated" else cyan)
                    print(f"  {colour(f'[{action.upper()}]')} Chassis: {enc.get('name', '?')}")
            except Exception as exc:
                stats["errors"] += 1
                print(f"  {red('[ERROR]')} Chassis {enc.get('name', '?')}: {exc}", file=sys.stderr)

        _print_stats("Chassis", stats)

    # ---- Servers ------------------------------------------------------------
    if not args.skip_servers:
        print("\nFetching server hardware from OneView …")
        servers = ov.get_server_hardware()
        if args.server_filter:
            servers = [s for s in servers
                       if _name_matches(
                           (s.get("serverName") or s.get("name") or "").split(".")[0],
                           args.server_filter,
                       )]
        print(f"  Found {len(servers)} server(s)\n")

        stats = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        for server in servers:
            try:
                labels = ov.get_resource_labels(server.get("uri", "")) if use_labels else []
                if use_labels and labels:
                    raw = (server.get("serverName") or server.get("name") or "?").split(".")[0]
                    print(f"  {cyan('[LABELS]')} {raw}: {', '.join(labels)}")
                action, device = syncer.sync_server(server, labels=labels)
                stats[action] += 1
                if not args.dry_run and action != "skipped":
                    colour = green if action == "created" else (yellow if action == "updated" else cyan)
                    display_name = device.name if device else server.get("name", "?")
                    print(f"  {colour(f'[{action.upper()}]')} Server: {display_name}")
            except Exception as exc:
                stats["errors"] += 1
                raw = (server.get("serverName") or server.get("name") or "?").split(".")[0]
                print(f"  {red('[ERROR]')} Server {raw}: {exc}", file=sys.stderr)

        _print_stats("Servers", stats)

    # ---- Deletions (servers before chassis to cleanly vacate device bays) ----
    if args.delete_missing:
        if not args.skip_servers:
            print("\nChecking for servers to remove from NetBox …")
            del_stats = syncer.delete_missing(args.server_role, syncer._seen_server_names)
            _print_delete_stats("Server", del_stats)

        if not args.skip_chassis:
            print("\nChecking for chassis to remove from NetBox …")
            del_stats = syncer.delete_missing(args.chassis_role, syncer._seen_chassis_names)
            _print_delete_stats("Chassis", del_stats)

    ov.logout()
    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}Done.")


if __name__ == "__main__":
    main()
