#!/usr/bin/env python3
"""
import_clients.py — bulk-import MAC addresses into AGNI client groups from a CSV.

Reads a CSV with a MAC column (``mac``, ``MACAddress``, ``mac_address``) and a
group column (``client_group``, ``EndpointGroup``, ``clientgroup``, ``group``,
``endpoint_group``).  Creates any client groups that don't already exist, writes
per-group CSV files, and uploads them via the
``identity.clients.clientGroup.import`` API.

Usage
-----
    uv run python script/import_clients.py script/sample_client_import.csv

Required env vars
-----------------
AGNI_HOST       Base URL (trailing slash).  Example: https://agni.lab.example.com/
AGNI_ORG_ID     Organisation ID.
AGNI_KEY_ID     API key ID.
AGNI_KEY_VALUE  API key secret.

Optional flags
--------------
--dry-run        Parse and validate the CSV without calling the API.
--zone-id N      Zone ID (default: 0).
--type TYPE      Client group type to use when creating groups (default: "").
--staging-dir D  Directory for per-group CSV files (default: scripts/ise_to_agni/tmp/).
"""

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pyagni import AGNI
from pyagni.exceptions import AGNIAuthError, AGNIRequestError
from rich import print as rprint
from rich.rule import Rule
from rich.table import Table
from rich.panel import Panel


# ── Helpers ───────────────────────────────────────────────────────────────────


def _check_env() -> dict:
    required = {
        "host": "AGNI_HOST",
        "org_id": "AGNI_ORG_ID",
        "key_id": "AGNI_KEY_ID",
        "key_value": "AGNI_KEY_VALUE",
    }
    missing = [v for v in required.values() if not os.getenv(v)]
    if missing:
        print("ERROR: the following environment variables must be set:")
        for var in missing:
            print(f"  {var}")
        sys.exit(1)
    return {k: os.getenv(v) for k, v in required.items()}


def _section(title: str) -> None:
    rprint(Rule(f"[bold cyan]{title}[/bold cyan]"))


def _ok(msg: str) -> None:
    rprint(f"  [green]✓[/green] {msg}")


def _warn(msg: str) -> None:
    rprint(f"  [yellow]![/yellow] {msg}")


def _fail(msg: str) -> None:
    rprint(f"  [red]✗[/red] {msg}")


def _default_staging_dir() -> str:
    d = os.path.join(os.path.dirname(__file__), "tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _normalize_mac(mac: str) -> str:
    """Strip separators and lowercase — AGNI expects 12-char hex."""
    return mac.replace(":", "").replace("-", "").replace(".", "").lower()


# ── CSV parsing ──────────────────────────────────────────────────────────────


_MAC_ALIASES = {"mac", "macaddress", "mac_address"}
_GROUP_ALIASES = {"client_group", "clientgroup", "endpointgroup", "endpoint_group", "group"}


def _resolve_column(fieldnames: list[str], aliases: set[str]) -> str | None:
    """Return the first fieldname whose lower-cased, stripped form matches an alias."""
    for fn in fieldnames:
        if fn.strip().lower() in aliases:
            return fn
    return None


def parse_csv(csv_path: str) -> dict[str, list[str]]:
    """Return {group_name: [mac, ...]} from a two-column CSV."""
    groups: dict[str, list[str]] = defaultdict(list)
    path = Path(csv_path)
    if not path.exists():
        print(f"ERROR: file not found: {csv_path}")
        sys.exit(1)

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            print("ERROR: CSV has no header row.")
            sys.exit(1)

        mac_col = _resolve_column(reader.fieldnames, _MAC_ALIASES)
        group_col = _resolve_column(reader.fieldnames, _GROUP_ALIASES)
        if not mac_col or not group_col:
            print(
                "ERROR: CSV must have a MAC column "
                f"({', '.join(sorted(_MAC_ALIASES))}) "
                f"and a group column ({', '.join(sorted(_GROUP_ALIASES))})."
            )
            sys.exit(1)

        for i, row in enumerate(reader, start=2):
            mac_raw = row[mac_col].strip()
            group = row[group_col].strip()
            if not mac_raw or not group:
                _warn(f"Row {i}: skipping empty mac or client_group")
                continue
            mac = _normalize_mac(mac_raw)
            if len(mac) != 12 or not all(c in "0123456789abcdef" for c in mac):
                _warn(f"Row {i}: invalid MAC '{mac_raw}', skipping")
                continue
            groups[group].append(mac)

    return dict(groups)


def write_group_csvs(groups: dict[str, list[str]], staging_dir: str) -> dict[str, str]:
    """Write per-group CSV files for the import API. Returns {group_name: file_path}."""
    paths = {}
    for group_name, macs in groups.items():
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in group_name)
        file_path = os.path.join(staging_dir, f"{safe_name}.csv")
        with open(file_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["mac", "description"])
            for mac in macs:
                colonized = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
                writer.writerow([colonized, ""])
        paths[group_name] = file_path
    return paths


# ── Main logic ───────────────────────────────────────────────────────────────


async def run_import(
    env: dict, groups: dict[str, list[str]], args: argparse.Namespace
) -> None:
    async with AGNI(
        name="import",
        org_id=env["org_id"],
        api_host=env["host"],
        key_id=env["key_id"],
        key_value=env["key_value"],
    ) as client:

        # ── 1. Fetch existing client groups and figure out what to create ─────
        _section("Client Groups")
        existing = await client.get_client_groups(zone_id=args.zone_id)
        existing_by_name = {cg["name"]: cg for cg in existing["import"]}
        _ok(f"{len(existing_by_name)} existing client group(s) on server")

        group_id_map: dict[str, int] = {}
        created_groups: list[str] = []
        reused_groups: list[str] = []

        for group_name in sorted(groups):
            if group_name in existing_by_name:
                group_id_map[group_name] = existing_by_name[group_name]["id"]
                reused_groups.append(group_name)
                _ok(f"Found existing group: {group_name} (id={group_id_map[group_name]})")
            else:
                result = await client.add_client_group(
                    name=group_name,
                    description=f"Auto-created by import_clients.py",
                    type=args.type,
                    zone_id=args.zone_id,
                )
                group_id_map[group_name] = result["import"]["id"]
                created_groups.append(group_name)
                _ok(f"Created group: {group_name} (id={group_id_map[group_name]})")

        # ── 2. Write per-group CSV files ─────────────────────────────────────
        _section("Staging Files")
        staging_dir = args.staging_dir or _default_staging_dir()
        csv_paths = write_group_csvs(groups, staging_dir)
        _ok(f"Wrote {len(csv_paths)} CSV file(s) to {staging_dir}")
        for gname, fpath in csv_paths.items():
            _ok(f"  {gname}: {fpath} ({len(groups[gname])} MAC(s))")

        # ── 3. Import each group's CSV ───────────────────────────────────────
        _section("Import")
        import_results: dict[str, dict] = {}

        for group_name in sorted(groups):
            gid = group_id_map[group_name]
            csv_file = csv_paths[group_name]
            try:
                result = await client.import_clients(
                    client_group_id=gid,
                    csv_path=csv_file,
                    zone_id=args.zone_id,
                )
                import_results[group_name] = result["import"]
                data = result["import"]
                inserted = data.get("insertCount", 0)
                updated = data.get("updateCount", 0)
                ignored = data.get("ignoreCount", 0)
                errors = data.get("errCount", 0)
                _ok(
                    f"{group_name}: {inserted} inserted, {updated} updated, "
                    f"{ignored} ignored, {errors} error(s)"
                )
                if errors and data.get("errors"):
                    for err in data["errors"]:
                        _warn(f"  {err.get('error', 'unknown')}: count={err.get('count', '?')}")
            except AGNIRequestError as exc:
                _fail(f"{group_name}: import failed — {exc}")
                import_results[group_name] = {"error": str(exc)}

        # ── 4. Report ────────────────────────────────────────────────────────
        _section("Summary")
        total_macs = sum(len(m) for m in groups.values())

        table = Table(
            "Client Group", "MACs in CSV", "Inserted", "Updated",
            "Ignored", "Errors", "Status",
            row_styles=["dim", ""],
        )
        for group_name in sorted(groups):
            mac_count = len(groups[group_name])
            r = import_results.get(group_name, {})
            if "error" in r:
                table.add_row(
                    group_name, str(mac_count),
                    "-", "-", "-", "-",
                    f"[red]FAILED[/red]",
                )
            else:
                status = "[green]OK[/green]" if r.get("errCount", 0) == 0 else "[yellow]PARTIAL[/yellow]"
                table.add_row(
                    group_name,
                    str(mac_count),
                    str(r.get("insertCount", 0)),
                    str(r.get("updateCount", 0)),
                    str(r.get("ignoreCount", 0)),
                    str(r.get("errCount", 0)),
                    status,
                )

        rprint(Panel(table, title="Import Results", title_align="left", expand=False))

        rprint()
        _ok(f"Total client groups: {len(groups)}")
        _ok(f"  Created: {len(created_groups)}")
        _ok(f"  Reused:  {len(reused_groups)}")
        _ok(f"Total MACs processed: {total_macs}")

        successful = [g for g, r in import_results.items() if "error" not in r]
        total_inserted = sum(import_results[g].get("insertCount", 0) for g in successful)
        total_updated = sum(import_results[g].get("updateCount", 0) for g in successful)
        total_errors = sum(import_results[g].get("errCount", 0) for g in successful)
        _ok(f"Total inserted: {total_inserted}")
        _ok(f"Total updated:  {total_updated}")
        if total_errors:
            _warn(f"Total errors:   {total_errors}")
        _ok(f"Staging dir:    {staging_dir}")


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv_file", help="Path to the CSV file with mac,client_group columns")
    parser.add_argument("--dry-run", action="store_true", help="Parse CSV only, don't call the API")
    parser.add_argument("--zone-id", type=int, default=0, help="Zone ID (default: 0)")
    parser.add_argument("--type", default="", help="Client group type for new groups (default: '')")
    parser.add_argument("--staging-dir", help="Directory for per-group CSV files (default: scripts/ise_to_agni/tmp/)")
    args = parser.parse_args()

    rprint(f"\n[bold]pyagni client import[/bold]  →  [cyan]{args.csv_file}[/cyan]\n")

    # ── Parse ──
    _section("Parsing CSV")
    groups = parse_csv(args.csv_file)
    if not groups:
        _fail("No valid entries found in CSV.")
        sys.exit(1)

    total_macs = sum(len(m) for m in groups.values())
    _ok(f"Found {len(groups)} client group(s), {total_macs} MAC address(es)")
    for gname, macs in sorted(groups.items()):
        _ok(f"  {gname}: {len(macs)} MAC(s)")

    if args.dry_run:
        _section("Dry Run")
        staging_dir = args.staging_dir or _default_staging_dir()
        csv_paths = write_group_csvs(groups, staging_dir)
        _ok(f"Would create {len(groups)} client group(s)")
        _ok(f"Staged {len(csv_paths)} CSV file(s) to {staging_dir}")
        for gname, fpath in csv_paths.items():
            _ok(f"  {fpath}")
        rprint("\n[bold yellow]Dry run complete — no API calls made.[/bold yellow]\n")
        return

    # ── Run ──
    env = _check_env()
    rprint(f"  Target: [cyan]{env['host']}[/cyan]  (org: {env['org_id']})\n")

    try:
        asyncio.run(run_import(env, groups, args))
    except AGNIAuthError as exc:
        _fail(f"Authentication failed: {exc}")
        sys.exit(1)

    rprint("\n[bold green]Import complete.[/bold green]\n")


if __name__ == "__main__":
    main()
