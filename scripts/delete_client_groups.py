#!/usr/bin/env python3
"""
delete_client_groups.py — delete client groups from an AGNI instance.

Usage
-----
    # Delete by exact name
    uv run python scripts/delete_client_groups.py --name "My Group"

    # Delete multiple by exact name
    uv run python scripts/delete_client_groups.py --name "Group A" --name "Group B"

    # Delete by partial name match (case-insensitive)
    uv run python scripts/delete_client_groups.py --match "ise_import"

    # Delete by partial description match (case-insensitive)
    uv run python scripts/delete_client_groups.py --desc-match "auto-created by automation"

    # Delete by group ID
    uv run python scripts/delete_client_groups.py --id 42

    # Preview what would be deleted
    uv run python scripts/delete_client_groups.py --match "test" --dry-run

Required env vars: AGNI_HOST, AGNI_ORG_ID, AGNI_KEY_ID, AGNI_KEY_VALUE
"""

import argparse
import asyncio
import os
import sys

import truststore
truststore.inject_into_ssl()

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


# ── Matching ──────────────────────────────────────────────────────────────────


def _find_targets(
    all_groups: list[dict],
    names: list[str] | None,
    patterns: list[str] | None,
    desc_patterns: list[str] | None,
    ids: list[int] | None,
) -> list[dict]:
    """Return the subset of groups that match any of the provided filters."""
    targets = []
    seen_ids = set()

    if names:
        for name in names:
            for cg in all_groups:
                if cg["name"] == name and cg["id"] not in seen_ids:
                    targets.append(cg)
                    seen_ids.add(cg["id"])

    if patterns:
        for pattern in patterns:
            pattern_lower = pattern.lower()
            for cg in all_groups:
                if pattern_lower in cg["name"].lower() and cg["id"] not in seen_ids:
                    targets.append(cg)
                    seen_ids.add(cg["id"])

    if desc_patterns:
        for pattern in desc_patterns:
            pattern_lower = pattern.lower()
            for cg in all_groups:
                desc = cg.get("description", "").lower()
                if pattern_lower in desc and cg["id"] not in seen_ids:
                    targets.append(cg)
                    seen_ids.add(cg["id"])

    if ids:
        for gid in ids:
            for cg in all_groups:
                if cg["id"] == gid and cg["id"] not in seen_ids:
                    targets.append(cg)
                    seen_ids.add(cg["id"])

    return targets


# ── Main logic ───────────────────────────────────────────────────────────────


async def run_delete(env: dict, args: argparse.Namespace) -> None:
    async with AGNI(
        name="delete",
        org_id=env["org_id"],
        api_host=env["host"],
        key_id=env["key_id"],
        key_value=env["key_value"],
    ) as client:

        # ── 1. Fetch existing client groups ──────────────────────────────────
        _section("Fetching Client Groups")
        existing = await client.get_client_groups(zone_id=args.zone_id)
        all_groups = existing["delete"]
        _ok(f"{len(all_groups)} client group(s) on server")

        # ── 2. Find targets ──────────────────────────────────────────────────
        _section("Matching")
        targets = _find_targets(all_groups, args.name, args.match, args.desc_match, args.id)

        if not targets:
            _warn("No client groups matched the given criteria.")
            return

        # ── 3. Count clients per group ───────────────────────────────────────
        group_clients: dict[int, list[str]] = {}
        for cg in targets:
            result = await client.get_clients(
                client_group_id=cg["id"], zone_id=args.zone_id,
            )
            clients = result["delete"]
            group_clients[cg["id"]] = [c["clientID"] for c in clients]

        table = Table("ID", "Name", "Clients", "Type", "Description", row_styles=["dim", ""])
        for cg in targets:
            table.add_row(
                str(cg["id"]),
                cg["name"],
                str(len(group_clients[cg["id"]])),
                cg.get("type", ""),
                cg.get("description", "")[:60],
            )
        total_clients = sum(len(c) for c in group_clients.values())
        rprint(Panel(
            table,
            title=f"{len(targets)} group(s), {total_clients} client(s) to delete",
            title_align="left",
            expand=False,
        ))

        # ── 4. Dry run or delete ─────────────────────────────────────────────
        if args.dry_run:
            _section("Dry Run")
            _warn(f"Would delete {total_clients} client(s) across {len(targets)} group(s). No API calls made.")
            return

        _section("Deleting")
        deleted_groups = 0
        deleted_clients = 0
        failed = 0
        for cg in targets:
            cg_name = cg["name"]
            cg_id = cg["id"]
            try:
                client_ids = group_clients[cg_id]
                if client_ids:
                    for i in range(0, len(client_ids), 100):
                        batch = client_ids[i : i + 100]
                        await client.delete_clients_bulk(
                            client_id_list=batch, zone_id=args.zone_id,
                        )
                    _ok(f"{cg_name}: deleted {len(client_ids)} client(s)")
                    deleted_clients += len(client_ids)

                await client.delete_client_group(
                    id=cg_id, name=cg_name, zone_id=args.zone_id,
                )
                _ok(f"{cg_name}: group deleted (id={cg_id})")
                deleted_groups += 1
            except AGNIRequestError as exc:
                _fail(f"{cg_name} (id={cg_id}): {exc}")
                failed += 1

        # ── 5. Summary ───────────────────────────────────────────────────────
        _section("Summary")
        _ok(f"Groups deleted:  {deleted_groups}")
        _ok(f"Clients deleted: {deleted_clients}")
        if failed:
            _warn(f"Failed: {failed}")


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--name", action="append",
        help="Exact client group name to delete (repeatable)",
    )
    parser.add_argument(
        "--match", action="append",
        help="Partial name match, case-insensitive (repeatable)",
    )
    parser.add_argument(
        "--desc-match", action="append",
        help="Partial description match, case-insensitive (repeatable)",
    )
    parser.add_argument(
        "--id", type=int, action="append",
        help="Client group ID to delete (repeatable)",
    )
    parser.add_argument("--zone-id", type=int, default=0, help="Zone ID (default: 0)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without calling the API")
    args = parser.parse_args()

    if not args.name and not args.match and not args.desc_match and not args.id:
        parser.error("at least one of --name, --match, --desc-match, or --id is required")

    env = _check_env()
    rprint(f"\n[bold]pyagni delete client groups[/bold]")
    rprint(f"  Target: [cyan]{env['host']}[/cyan]  (org: {env['org_id']})\n")

    try:
        asyncio.run(run_delete(env, args))
    except AGNIAuthError as exc:
        _fail(f"Authentication failed: {exc}")
        sys.exit(1)

    rprint()


if __name__ == "__main__":
    main()
