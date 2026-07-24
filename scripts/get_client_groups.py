#!/usr/bin/env python3
"""
get_client_groups.py — list all client groups from an AGNI instance.

Usage
-----
    uv run python script/get_client_groups.py
    uv run python script/get_client_groups.py --ca-cert /path/to/ca-bundle.crt
    uv run python script/get_client_groups.py --no-verify

Required env vars: AGNI_HOST, AGNI_ORG_ID, AGNI_KEY_ID, AGNI_KEY_VALUE

SSL / TLS
---------
You can also set the AGNI_SSL_VERIFY env var instead of using flags:
    AGNI_SSL_VERIFY=false           Skip certificate verification
    AGNI_SSL_VERIFY=/path/to/ca.crt Use a custom CA bundle
"""

import argparse
import asyncio
import os
import sys

import truststore
truststore.inject_into_ssl()

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


def _resolve_verify(args: argparse.Namespace):
    """CLI flags take precedence over the AGNI_SSL_VERIFY env var."""
    if args.no_verify:
        return False
    if args.ca_cert:
        return args.ca_cert
    return None


async def run(env: dict, verify) -> None:
    async with AGNI(
        name="query",
        org_id=env["org_id"],
        api_host=env["host"],
        key_id=env["key_id"],
        key_value=env["key_value"],
        verify=verify,
    ) as client:
        result = await client.get_client_groups()
        utils.print_client_groups([result])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument(
        "--ca-cert",
        metavar="PATH",
        help="Path to a CA certificate bundle (PEM) for TLS verification",
    )
    ssl_group.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable TLS certificate verification (not recommended for production)",
    )
    args = parser.parse_args()

    env = _check_env()
    verify = _resolve_verify(args)

    try:
        asyncio.run(run(env, verify))
    except AGNIAuthError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
