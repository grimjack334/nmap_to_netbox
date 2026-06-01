#!/usr/bin/env python3
"""
nmap_to_netbox.py
-----------------
Parse nmap XML scan output and import IP addresses (with status and reverse
DNS) into NetBox as IP address objects.

Features:
  - Reserves the first N IPs in a prefix (default 20) with status "reserved"
  - Reserves the last M IPs in a prefix (default 5) with status "reserved"
  - Tags every scanned IP with an nmap-scan tag (auto-created if missing)
  - Dry-run shows a field-by-field diff against live NetBox values
  - Protected statuses (reserved, dhcp, slaac) are never overwritten by scan data

Requirements:
    pip install pynetbox

Usage:
    # Run nmap with XML output first:
    #   nmap -sn -R --dns-servers 8.8.8.8 192.168.1.0/24 -oX scan.xml

    python nmap_to_netbox.py \\
        --xml scan.xml \\
        --netbox-url https://netbox.example.com \\
        --token YOUR_API_TOKEN \\
        [--prefix 192.168.1.0/24] \\
        [--reserve-head 20] \\
        [--reserve-tail 5] \\
        [--scan-tag nmap-scan] \\
        [--dns-suffix .example.com] \\
        [--include-down] \\
        [--live-rdns] \\
        [--dry-run]
"""

import argparse
import ipaddress
import socket
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

try:
    import pynetbox
except ImportError:
    sys.exit("Missing dependency: pip install pynetbox")


# ---------------------------------------------------------------------------
# Statuses that must never be overwritten by scan data
# ---------------------------------------------------------------------------

# NetBox status values that represent intentional administrative assignments.
# A scanned host returning "up" or "down" should never clobber these.
PROTECTED_STATUSES = {"reserved", "dhcp", "slaac"}


# ---------------------------------------------------------------------------
# ANSI colours (disabled automatically when not a TTY)
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
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ScannedHost:
    ip: str
    status: str          # "active" | "deprecated"  (maps from nmap up/down)
    rdns: Optional[str] = None
    mac: Optional[str] = None
    vendor: Optional[str] = None
    hostnames: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# nmap XML parser
# ---------------------------------------------------------------------------

def parse_nmap_xml(xml_path: str, include_down: bool = False) -> list[ScannedHost]:
    """Parse an nmap XML report and return a list of ScannedHost objects."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        sys.exit(f"Failed to parse XML: {exc}")

    hosts: list[ScannedHost] = []

    for host_el in tree.getroot().findall("host"):
        status_el = host_el.find("status")
        state = status_el.get("state", "down") if status_el is not None else "down"

        if state == "down" and not include_down:
            continue

        nb_status = "active" if state == "up" else "deprecated"

        ip = mac = vendor = None
        for addr_el in host_el.findall("address"):
            atype = addr_el.get("addrtype")
            if atype == "ipv4":
                ip = addr_el.get("addr")
            elif atype == "ipv6" and ip is None:
                ip = addr_el.get("addr")
            elif atype == "mac":
                mac  = addr_el.get("addr")
                vendor = addr_el.get("vendor")

        if not ip:
            continue

        hostnames: list[str] = []
        rdns: Optional[str] = None
        hostnames_el = host_el.find("hostnames")
        if hostnames_el is not None:
            for hn in hostnames_el.findall("hostname"):
                name  = hn.get("name", "")
                htype = hn.get("type", "")
                if name:
                    hostnames.append(name)
                    if htype == "PTR" and rdns is None:
                        rdns = name

        hosts.append(ScannedHost(
            ip=ip, status=nb_status, rdns=rdns,
            mac=mac, vendor=vendor, hostnames=hostnames,
        ))

    return hosts


# ---------------------------------------------------------------------------
# Optional live reverse-DNS fallback
# ---------------------------------------------------------------------------

def resolve_rdns(ip: str, timeout: float = 2.0) -> Optional[str]:
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None
    finally:
        socket.setdefaulttimeout(old_timeout)


# ---------------------------------------------------------------------------
# Dry-run diff helpers
# ---------------------------------------------------------------------------

def _tag_slugs(nb_obj) -> set[str]:
    """Return the set of tag slugs on an existing NetBox object."""
    try:
        return {t.slug for t in (nb_obj.tags or [])}
    except Exception:
        return set()


def _status_val(nb_obj) -> str:
    """Normalise status to a plain string regardless of pynetbox version."""
    try:
        return nb_obj.status.value
    except AttributeError:
        return str(nb_obj.status)


def diff_fields(existing, desired: dict, scan_tag_slug: str) -> list[str]:
    """
    Compare desired payload against a live NetBox IP object.
    Returns a list of human-readable change lines.
    Status changes are suppressed when the existing status is protected.
    """
    lines = []

    # address
    if existing.address != desired["address"]:
        lines.append(f"  address:  {red(existing.address)} → {green(desired['address'])}")

    # status — skip if the existing status is protected
    cur_status = _status_val(existing)
    if cur_status in PROTECTED_STATUSES:
        if cur_status != desired["status"]:
            lines.append(
                f"  status:   {cyan(cur_status)} {bold('(protected — will not change)')}"
            )
    elif cur_status != desired["status"]:
        lines.append(f"  status:   {red(cur_status)} → {green(desired['status'])}")

    # dns_name
    cur_dns = existing.dns_name or ""
    new_dns = desired.get("dns_name", "")
    if cur_dns != new_dns:
        lines.append(f"  dns_name: {red(repr(cur_dns))} → {green(repr(new_dns))}")

    # tags — will the scan tag be added?
    cur_tags = _tag_slugs(existing)
    if scan_tag_slug not in cur_tags:
        lines.append(f"  tags:     add {green(scan_tag_slug)}")

    # custom_fields.mac_address
    new_mac = (desired.get("custom_fields") or {}).get("mac_address")
    cur_mac = None
    try:
        cur_mac = existing.custom_fields.get("mac_address")
    except Exception:
        pass
    if new_mac and cur_mac != new_mac:
        lines.append(f"  mac:      {red(repr(cur_mac))} → {green(repr(new_mac))}")

    return lines


# ---------------------------------------------------------------------------
# NetBox importer
# ---------------------------------------------------------------------------

class NetBoxImporter:
    def __init__(self, url: str, token: str, dry_run: bool = False):
        self.dry_run  = dry_run
        self.nb       = pynetbox.api(url, token=token)
        self._tag_cache: dict[str, object] = {}
        try:
            self.nb.status()
        except Exception as exc:
            sys.exit(f"Cannot reach NetBox at {url}: {exc}")

    # ---- helpers -----------------------------------------------------------

    def _cidr(self, ip: str, prefix: Optional[str]) -> str:
        if prefix:
            net = ipaddress.ip_network(prefix, strict=False)
            return f"{ip}/{net.prefixlen}"
        addr = ipaddress.ip_address(ip)
        return f"{ip}/{'32' if addr.version == 4 else '128'}"

    def _get_or_create_tag(self, slug: str, name: str, colour: str = "blue") -> object:
        """Return a NetBox tag object, creating it if it doesn't exist."""
        if slug in self._tag_cache:
            return self._tag_cache[slug]
        existing = list(self.nb.extras.tags.filter(slug=slug))
        if existing:
            tag = existing[0]
        else:
            if self.dry_run:
                # return a lightweight stub so the rest of the code works
                class _Stub:
                    pass
                tag = _Stub()
                tag.slug = slug
                tag.id   = None
                print(f"  {yellow('[DRY-RUN]')} would create tag: {bold(name)} (slug={slug})")
            else:
                tag = self.nb.extras.tags.create(
                    name=name, slug=slug, color=colour,
                    description="Created by nmap_to_netbox.py"
                )
                print(f"  {green('[TAG CREATED]')} {name} (slug={slug})")
        self._tag_cache[slug] = tag
        return tag

    # ---- reservation helpers -----------------------------------------------

    def _reserve_addresses(
        self,
        addrs: list,
        prefix_len: int,
        scan_tag_slug: str,
        label: str,
    ) -> None:
        """Write/verify a list of addresses as reserved. Shared by head and tail."""
        for addr in addrs:
            cidr     = f"{addr}/{prefix_len}"
            existing = list(self.nb.ipam.ip_addresses.filter(address=cidr))

            if self.dry_run:
                if existing:
                    cur_status = _status_val(existing[0])
                    cur_tags   = _tag_slugs(existing[0])
                    changes: list[str] = []
                    if cur_status != "reserved":
                        changes.append(f"  status: {red(cur_status)} → {green('reserved')}")
                    if scan_tag_slug not in cur_tags:
                        changes.append(f"  tags:   add {green(scan_tag_slug)}")
                    if changes:
                        print(f"  {yellow('[DRY-RUN UPDATE]')} {cidr}")
                        for c in changes:
                            print(c)
                    else:
                        print(f"  {cyan('[NO CHANGE]')} {cidr}  already reserved")
                else:
                    print(f"  {yellow('[DRY-RUN CREATE]')} {cidr}  status={green('reserved')}")
                continue

            payload = {
                "address": cidr,
                "status":  "reserved",
                "tags":    [{"slug": scan_tag_slug}],
            }
            if existing:
                obj        = existing[0]
                cur_tags   = _tag_slugs(obj)
                cur_status = _status_val(obj)
                needs_tag    = scan_tag_slug not in cur_tags
                needs_status = cur_status != "reserved"
                if needs_status or needs_tag:
                    # Merge tags
                    payload["tags"] = [{"slug": s} for s in (cur_tags | {scan_tag_slug})]
                    obj.update(payload)
                    print(f"  {yellow('[UPDATED→RESERVED]')} {cidr}  (was {cur_status})")
                else:
                    print(f"  {cyan('[SKIP]')} {cidr}  already reserved")
            else:
                self.nb.ipam.ip_addresses.create(**payload)
                print(f"  {green('[RESERVED]')} {cidr}")

    def reserve_prefix_head(
        self,
        prefix: str,
        count: int,
        scan_tag_slug: str,
        scan_tag_name: str,
    ) -> None:
        """Reserve the first `count` host addresses in `prefix`."""
        net   = ipaddress.ip_network(prefix, strict=False)
        hosts = list(net.hosts())
        to_reserve = hosts[:count]
        print(f"\nReserving first {len(to_reserve)} address(es) in {prefix} …")
        self._get_or_create_tag(scan_tag_slug, scan_tag_name)
        self._reserve_addresses(to_reserve, net.prefixlen, scan_tag_slug, "head")

    def reserve_prefix_tail(
        self,
        prefix: str,
        count: int,
        scan_tag_slug: str,
        scan_tag_name: str,
    ) -> None:
        """Reserve the last `count` host addresses in `prefix`."""
        net   = ipaddress.ip_network(prefix, strict=False)
        hosts = list(net.hosts())
        to_reserve = hosts[-count:] if count <= len(hosts) else hosts
        print(f"\nReserving last {len(to_reserve)} address(es) in {prefix} …")
        self._get_or_create_tag(scan_tag_slug, scan_tag_name)
        self._reserve_addresses(to_reserve, net.prefixlen, scan_tag_slug, "tail")

    # ---- upsert ------------------------------------------------------------

    def upsert(
        self,
        host: ScannedHost,
        prefix:        Optional[str] = None,
        dns_suffix:    Optional[str] = None,
        scan_tag_slug: str           = "nmap-scan",
        scan_tag_name: str           = "nmap-scan",
    ) -> tuple[str, object]:
        """
        Create or update an IP address record in NetBox.
        Returns (action, nb_object) where action is 'created'|'updated'|'unchanged'.
        """
        cidr = self._cidr(host.ip, prefix)

        dns_name = host.rdns or ""
        if dns_name and dns_suffix and not dns_name.endswith(dns_suffix):
            dns_name = dns_name.rstrip(".") + dns_suffix

        tag = self._get_or_create_tag(scan_tag_slug, scan_tag_name)

        payload: dict = {
            "address":  cidr,
            "status":   host.status,
            "dns_name": dns_name,
            "tags":     [{"slug": scan_tag_slug}],
        }
        if host.mac:
            payload["custom_fields"] = {"mac_address": host.mac}

        existing = list(self.nb.ipam.ip_addresses.filter(address=cidr))

        # ---- dry-run: show diff --------------------------------------------
        if self.dry_run:
            if existing:
                cur_status = _status_val(existing[0])
                if cur_status in PROTECTED_STATUSES:
                    changes = diff_fields(existing[0], payload, scan_tag_slug)
                    # Remove any status line — we won't change it
                    changes = [l for l in changes if "status" not in l or "protected" in l]
                    if changes:
                        print(f"  {yellow('[DRY-RUN UPDATE]')} {cidr}  {cyan(f'(status={cur_status} protected)')}")
                        for line in changes:
                            print(line)
                        return "updated", None
                    else:
                        print(f"  {cyan('[DRY-RUN NO CHANGE]')} {cidr}  {cyan(f'(status={cur_status} protected)')}")
                        return "unchanged", None
                changes = diff_fields(existing[0], payload, scan_tag_slug)
                if changes:
                    print(f"  {yellow('[DRY-RUN UPDATE]')} {cidr}")
                    for line in changes:
                        print(line)
                    return "updated", None
                else:
                    print(f"  {cyan('[DRY-RUN NO CHANGE]')} {cidr}")
                    return "unchanged", None
            else:
                vendor_str = f"  vendor={host.vendor}" if host.vendor else ""
                dns_str    = f"  dns={dns_name}" if dns_name else ""
                print(f"  {green('[DRY-RUN CREATE]')} {cidr}  status={host.status}{dns_str}{vendor_str}")
                return "created", None

        # ---- live write ----------------------------------------------------
        if existing:
            obj        = existing[0]
            cur_status = _status_val(obj)

            # Never overwrite a protected status with scan-derived data
            if cur_status in PROTECTED_STATUSES:
                payload["status"] = cur_status

            changes = diff_fields(obj, payload, scan_tag_slug)
            if changes:
                # Merge tags rather than replacing them
                cur_tag_slugs = _tag_slugs(obj)
                payload["tags"] = [{"slug": s} for s in (cur_tag_slugs | {scan_tag_slug})]
                obj.update(payload)
                return "updated", obj
            return "unchanged", obj
        else:
            obj = self.nb.ipam.ip_addresses.create(**payload)
            return "created", obj


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Import nmap XML scan results into NetBox IPAM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--xml",         required=True,  help="Path to nmap XML output file")
    p.add_argument("--netbox-url",  required=True,  help="NetBox base URL (no trailing slash)")
    p.add_argument("--token",       required=True,  help="NetBox API token")
    p.add_argument("--prefix",      default=None,
                   help="CIDR prefix (e.g. 192.168.1.0/24). Sets mask on all IPs and "
                        "controls which addresses get reserved.")
    p.add_argument("--reserve-head", type=int, default=20,
                   help="Number of IPs at the start of --prefix to mark reserved (default: 20)")
    p.add_argument("--reserve-tail", type=int, default=5,
                   help="Number of IPs at the end of --prefix to mark reserved (default: 5)")
    p.add_argument("--scan-tag",    default="nmap-scan",
                   help="Tag slug applied to every scanned IP (default: nmap-scan). "
                        "Tag is auto-created if absent.")
    p.add_argument("--dns-suffix",  default=None,
                   help="Append suffix to rDNS names (e.g. .corp.example.com)")
    p.add_argument("--include-down", action="store_true",
                   help="Also import hosts that were down (status=deprecated)")
    p.add_argument("--live-rdns",   action="store_true",
                   help="Perform live PTR lookups for IPs with no rDNS in the XML")
    p.add_argument("--dry-run",     action="store_true",
                   help="Preview all changes with field-level diffs; nothing is written")
    return p


def main() -> None:
    args = build_parser().parse_args()

    tag_slug = args.scan_tag
    tag_name = args.scan_tag   # same string; customise if you want a display name

    if args.dry_run:
        print(bold("=== DRY RUN — no changes will be written ===\n"))

    print(f"Parsing {args.xml} …")
    hosts = parse_nmap_xml(args.xml, include_down=args.include_down)
    print(f"  Found {len(hosts)} host(s) to process.")

    if args.live_rdns:
        print("Performing live reverse DNS lookups for hosts without PTR …")
        for h in hosts:
            if not h.rdns:
                h.rdns = resolve_rdns(h.ip)

    importer = NetBoxImporter(args.netbox_url, args.token, dry_run=args.dry_run)

    # Step 1 — reserve head of prefix
    if args.prefix and args.reserve_head > 0:
        importer.reserve_prefix_head(
            prefix        = args.prefix,
            count         = args.reserve_head,
            scan_tag_slug = tag_slug,
            scan_tag_name = tag_name,
        )

    # Step 2 — reserve tail of prefix
    if args.prefix and args.reserve_tail > 0:
        importer.reserve_prefix_tail(
            prefix        = args.prefix,
            count         = args.reserve_tail,
            scan_tag_slug = tag_slug,
            scan_tag_name = tag_name,
        )

    # Step 3 — upsert scanned hosts
    print(f"\nProcessing {len(hosts)} scanned host(s) …")
    stats = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0}

    for host in hosts:
        try:
            action, _ = importer.upsert(
                host,
                prefix        = args.prefix,
                dns_suffix    = args.dns_suffix,
                scan_tag_slug = tag_slug,
                scan_tag_name = tag_name,
            )
            stats[action] += 1
            if not args.dry_run:
                dns_str = f"  dns={host.rdns}" if host.rdns else ""
                colour  = green if action == "created" else (yellow if action == "updated" else cyan)
                print(f"  {colour(f'[{action.upper()}]')} {host.ip}{dns_str}  status={host.status}")
        except Exception as exc:
            stats["errors"] += 1
            print(f"  {red('[ERROR]')} {host.ip}: {exc}", file=sys.stderr)

    print(
        f"\n{'[DRY-RUN] ' if args.dry_run else ''}Done. "
        f"Created: {stats['created']}  "
        f"Updated: {stats['updated']}  "
        f"Unchanged: {stats['unchanged']}  "
        f"Errors: {stats['errors']}"
    )


if __name__ == "__main__":
    main()
