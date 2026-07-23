#!/usr/bin/env python3
"""
get_client_groups.py — list all client groups from an AGNI instance.

Usage
-----
    uv run python script/get_client_groups.py

Required env vars: AGNI_HOST, AGNI_ORG_ID, AGNI_KEY_ID, AGNI_KEY_VALUE
"""

import asyncio
import os
import sys
from pprint import pprint

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pyagni import AGNI, utils
from pyagni.exceptions import AGNIAuthError


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


async def main() -> None:
    env = _check_env()
    async with AGNI(
        name="query",
        org_id=env["org_id"],
        api_host=env["host"],
        key_id=env["key_id"],
        key_value=env["key_value"],
    ) as client:
        result = await client.get_client_groups()
        # pprint(result)
        utils.print_client_groups([result])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AGNIAuthError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        sys.exit(1)
