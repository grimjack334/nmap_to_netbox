#!/usr/bin/env python3
"""
scan_tagged_prefixes.py
-----------------------
Wrapper that:
  1. Queries NetBox for all prefixes carrying a specific tag (default: nmap-scan)
  2. Runs nmap against each prefix  (-sn -R --dns-servers … -oX <tmpfile>)
  3. Calls nmap_to_netbox.py for each scan result
  4. Prints a final summary across all prefixes

The wrapper and the importer share the same NetBox URL / token so you only
need to supply credentials once.  All nmap_to_netbox.py flags are forwarded
automatically; overrides can be passed after --.

Requirements:
    pip install pynetbox
    nmap must be in PATH (https://nmap.org)
    nmap_to_netbox.py must be in the same directory as this script (or on PATH)

Usage:
    # Basic — scan every prefix tagged "nmap-scan" in NetBox
    python scan_tagged_prefixes.py \\
        --netbox-url https://netbox.example.com \\
        --token YOUR_API_TOKEN

    # Custom tag, extra nmap flags, dry-run everything
    python scan_tagged_prefixes.py \\
        --netbox-url https://netbox.example.com \\
        --token YOUR_API_TOKEN \\
        --prefix-tag nmap-scan \\
        --scan-tag   nmap-scan \\
        --dns-servers 192.168.1.1,8.8.8.8 \\
        --nmap-extra "-T4 --min-parallelism 64" \\
        --reserve-head 20 \\
        --reserve-tail 5 \\
        --include-down \\
        --live-rdns \\
        --dry-run

    # Skip specific prefixes (CIDR exact match, comma-separated)
    python scan_tagged_prefixes.py \\
        --netbox-url https://netbox.example.com \\
        --token YOUR_API_TOKEN \\
        --skip-prefixes 10.0.0.0/8,172.16.0.0/12
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

try:
    import pynetbox
except ImportError:
    sys.exit("Missing dependency: pip install pynetbox")


# ---------------------------------------------------------------------------
# ANSI colours
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
dim    = lambda t: _colour("2",  t)


# ---------------------------------------------------------------------------
# NetBox prefix fetcher
# ---------------------------------------------------------------------------

def fetch_tagged_prefixes(
    nb: "pynetbox.api",
    tag_slug: str,
    skip: set[str],
    min_prefix_len: Optional[int],
    max_prefix_len: Optional[int],
) -> list[dict]:
    """
    Return a list of dicts with keys: prefix, description, vrf, site.
    Filters out any prefixes in `skip`, and optionally by prefix length.
    """
    results = list(nb.ipam.prefixes.filter(tag=tag_slug))
    prefixes = []
    for p in results:
        cidr = str(p.prefix)
        if cidr in skip:
            print(f"  {dim('[SKIP]')} {cidr}  (in --skip-prefixes)")
            continue
        # prefix-length filter
        bits = int(cidr.split("/")[1])
        if min_prefix_len is not None and bits < min_prefix_len:
            print(f"  {dim('[SKIP]')} {cidr}  (/{bits} < min {min_prefix_len})")
            continue
        if max_prefix_len is not None and bits > max_prefix_len:
            print(f"  {dim('[SKIP]')} {cidr}  (/{bits} > max {max_prefix_len})")
            continue
        prefixes.append({
            "prefix":      cidr,
            "description": str(p.description or ""),
            "vrf":         str(p.vrf) if p.vrf else None,
            "site":        str(p.site) if p.site else None,
        })
    return prefixes


# ---------------------------------------------------------------------------
# nmap runner
# ---------------------------------------------------------------------------

def run_nmap(
    prefix: str,
    dns_servers: Optional[str],
    extra_flags: str,
    output_xml: str,
) -> bool:
    """
    Execute nmap against `prefix`, writing XML to `output_xml`.
    Returns True on success.
    """
    cmd = ["nmap", "-sn", "-R"]
    if dns_servers:
        cmd += ["--dns-servers", dns_servers]
    if extra_flags:
        cmd += extra_flags.split()
    cmd += ["-oX", output_xml, prefix]

    print(f"  {cyan('$')} {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(red(f"  [NMAP ERROR] exit {result.returncode}"))
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
        return False

    return True


# ---------------------------------------------------------------------------
# nmap_to_netbox.py runner
# ---------------------------------------------------------------------------

def find_importer(explicit: Optional[str]) -> str:
    """Locate nmap_to_netbox.py: explicit path → same dir → PATH."""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            sys.exit(f"--importer path not found: {explicit}")
        return str(p)
    # same directory as this wrapper
    local = Path(__file__).parent / "nmap_to_netbox.py"
    if local.is_file():
        return str(local)
    # fall back to PATH
    found = shutil.which("nmap_to_netbox.py")
    if found:
        return found
    sys.exit(
        "Cannot find nmap_to_netbox.py. "
        "Put it in the same directory as this script or pass --importer."
    )


def run_importer(
    importer_path: str,
    xml_path: str,
    prefix: str,
    args: argparse.Namespace,
) -> int:
    """
    Build and run the nmap_to_netbox.py command for a single prefix.
    Returns the process exit code.
    """
    cmd = [
        sys.executable, importer_path,
        "--xml",         xml_path,
        "--netbox-url",  args.netbox_url,
        "--token",       args.token,
        "--prefix",      prefix,
        "--reserve-head", str(args.reserve_head),
        "--reserve-tail", str(args.reserve_tail),
        "--scan-tag",    args.scan_tag,
    ]
    if args.dns_suffix:
        cmd += ["--dns-suffix", args.dns_suffix]
    if args.include_down:
        cmd.append("--include-down")
    if args.live_rdns:
        cmd.append("--live-rdns")
    if args.dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, text=True)
    return result.returncode


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(results: list[dict]) -> None:
    total      = len(results)
    succeeded  = sum(1 for r in results if r["status"] == "ok")
    nmap_fail  = sum(1 for r in results if r["status"] == "nmap_error")
    import_fail= sum(1 for r in results if r["status"] == "import_error")
    skipped    = sum(1 for r in results if r["status"] == "skipped")

    print()
    print(bold("=" * 60))
    print(bold("  SCAN SUMMARY"))
    print(bold("=" * 60))
    print(f"  Total prefixes : {total}")
    print(f"  {green('Succeeded')}      : {succeeded}")
    print(f"  {yellow('Skipped')}        : {skipped}")
    print(f"  {red('nmap errors')}    : {nmap_fail}")
    print(f"  {red('Import errors')} : {import_fail}")
    print()

    if nmap_fail or import_fail:
        print(bold("  Failed prefixes:"))
        for r in results:
            if r["status"] not in ("ok", "skipped"):
                elapsed = f"{r['elapsed']:.1f}s" if r.get("elapsed") else ""
                print(f"    {red('✗')} {r['prefix']:30s}  [{r['status']}]  {elapsed}")
        print()

    print(bold("  All prefixes:"))
    for r in results:
        symbol  = green("✓") if r["status"] == "ok" else (dim("–") if r["status"] == "skipped" else red("✗"))
        elapsed = f"  {r['elapsed']:.1f}s" if r.get("elapsed") else ""
        desc    = f"  {dim(r['description'])}" if r.get("description") else ""
        print(f"    {symbol} {r['prefix']:30s}{elapsed}{desc}")
    print(bold("=" * 60))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scan all NetBox-tagged prefixes with nmap and import results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__),
    )

    # --- NetBox connection ---
    nb = p.add_argument_group("NetBox connection")
    nb.add_argument("--netbox-url",  required=True,
                    help="NetBox base URL, e.g. https://netbox.example.com")
    nb.add_argument("--token",       required=True,
                    help="NetBox API token")

    # --- Prefix selection ---
    sel = p.add_argument_group("Prefix selection")
    sel.add_argument("--prefix-tag", default="nmap-scan",
                     help="NetBox tag slug used to select prefixes (default: nmap-scan)")
    sel.add_argument("--skip-prefixes", default="",
                     help="Comma-separated CIDRs to skip, e.g. 10.0.0.0/8,172.16.0.0/12")
    sel.add_argument("--min-prefix-len", type=int, default=None,
                     help="Skip prefixes shorter than this (e.g. 16 skips /8, /15)")
    sel.add_argument("--max-prefix-len", type=int, default=None,
                     help="Skip prefixes longer than this (e.g. 28 skips /29, /30 …)")

    # --- nmap options ---
    nm = p.add_argument_group("nmap options")
    nm.add_argument("--dns-servers", default=None,
                    help="Comma-separated DNS servers passed to nmap --dns-servers")
    nm.add_argument("--nmap-extra",  default="",
                    help='Extra nmap flags, e.g. "-T4 --min-parallelism 64"')

    # --- importer options (forwarded to nmap_to_netbox.py) ---
    imp = p.add_argument_group("importer options (forwarded to nmap_to_netbox.py)")
    imp.add_argument("--scan-tag",    default="nmap-scan",
                     help="Tag slug applied to every scanned IP (default: nmap-scan)")
    imp.add_argument("--reserve-head", type=int, default=20,
                     help="First N IPs per prefix to mark reserved (default: 20)")
    imp.add_argument("--reserve-tail", type=int, default=5,
                     help="Last N IPs per prefix to mark reserved (default: 5)")
    imp.add_argument("--dns-suffix",  default=None,
                     help="Append DNS suffix to rDNS names (e.g. .corp.example.com)")
    imp.add_argument("--include-down", action="store_true",
                     help="Import down hosts as deprecated")
    imp.add_argument("--live-rdns",   action="store_true",
                     help="Perform live PTR lookups for IPs missing rDNS in XML")

    # --- misc ---
    misc = p.add_argument_group("misc")
    misc.add_argument("--importer",   default=None,
                      help="Explicit path to nmap_to_netbox.py (auto-detected if omitted)")
    misc.add_argument("--tmpdir",     default=None,
                      help="Directory for temporary nmap XML files (default: system temp)")
    misc.add_argument("--dry-run",    action="store_true",
                      help="Run nmap for real but only diff against NetBox — nothing is written")
    misc.add_argument("--pause",      type=float, default=0,
                      help="Seconds to pause between prefixes (default: 0)")

    return p


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    args = build_parser().parse_args()

    if args.dry_run:
        print(bold("=== DRY RUN — nmap will run; NetBox will not be modified ===\n"))

    # --- pre-flight checks --------------------------------------------------
    if not shutil.which("nmap"):
        sys.exit("nmap not found in PATH. Install nmap or run with --dry-run.")

    importer_path = find_importer(args.importer)
    print(f"Importer  : {importer_path}")

    # --- connect to NetBox --------------------------------------------------
    print(f"NetBox    : {args.netbox_url}")
    try:
        nb = pynetbox.api(args.netbox_url, token=args.token)
        nb.status()
    except Exception as exc:
        sys.exit(f"Cannot reach NetBox: {exc}")

    # --- fetch prefixes -----------------------------------------------------
    skip_set = {s.strip() for s in args.skip_prefixes.split(",") if s.strip()}
    print(f"\nFetching prefixes tagged {bold(args.prefix_tag)} …")
    prefixes = fetch_tagged_prefixes(
        nb,
        tag_slug       = args.prefix_tag,
        skip           = skip_set,
        min_prefix_len = args.min_prefix_len,
        max_prefix_len = args.max_prefix_len,
    )

    if not prefixes:
        print(yellow("No eligible prefixes found. Nothing to do."))
        sys.exit(0)

    print(f"  Found {bold(str(len(prefixes)))} prefix(es) to scan:\n")
    for p in prefixes:
        site_str = f"  site={p['site']}" if p["site"] else ""
        vrf_str  = f"  vrf={p['vrf']}"   if p["vrf"]  else ""
        desc_str = f"  {dim(p['description'])}" if p["description"] else ""
        print(f"    {cyan(p['prefix']):30s}{site_str}{vrf_str}{desc_str}")
    print()

    # --- scan loop ----------------------------------------------------------
    results: list[dict] = []

    with tempfile.TemporaryDirectory(dir=args.tmpdir) as tmpdir:
        for idx, pinfo in enumerate(prefixes, 1):
            prefix = pinfo["prefix"]
            header = f"[{idx}/{len(prefixes)}] {bold(prefix)}"
            if pinfo["description"]:
                header += f"  {dim(pinfo['description'])}"
            print(bold("─" * 60))
            print(header)
            print(bold("─" * 60))

            xml_file  = os.path.join(tmpdir, f"scan_{prefix.replace('/', '_')}.xml")
            t_start   = time.monotonic()
            run_result = {"prefix": prefix, "description": pinfo["description"],
                          "status": "ok", "elapsed": None}

            # --- nmap -------------------------------------------------------
            print(f"\n{bold('Step 1:')} nmap scan")
            ok = run_nmap(
                prefix      = prefix,
                dns_servers = args.dns_servers,
                extra_flags = args.nmap_extra,
                output_xml  = xml_file,
            )
            if not ok:
                run_result["status"]  = "nmap_error"
                run_result["elapsed"] = time.monotonic() - t_start
                results.append(run_result)
                print(red(f"  Skipping import for {prefix} due to nmap failure."))
                if args.pause:
                    time.sleep(args.pause)
                continue

            # --- importer ---------------------------------------------------
            print(f"\n{bold('Step 2:')} import into NetBox")
            rc = run_importer(importer_path, xml_file, prefix, args)
            if rc != 0:
                run_result["status"] = "import_error"

            run_result["elapsed"] = time.monotonic() - t_start
            results.append(run_result)

            if args.pause and idx < len(prefixes):
                print(f"\n{dim(f'Pausing {args.pause}s …')}")
                time.sleep(args.pause)

    # --- summary ------------------------------------------------------------
    print_summary(results)

    # Exit non-zero if any prefix failed
    if any(r["status"] not in ("ok", "skipped") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
