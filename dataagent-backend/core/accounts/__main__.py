"""CLI utility to bootstrap local users/admins in da_user.

Usage:
    python -m core.accounts create-user --username admin --password '...' --admin
"""
from __future__ import annotations

import argparse
import json
import sys

from core.accounts.service import ensure_local_user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.accounts")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="Create or update a local user account")
    create.add_argument("--username", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--display-name", default="")
    create.add_argument("--admin", action="store_true", help="Assign admin role in addition to user role")

    args = parser.parse_args(argv)
    if args.command == "create-user":
        user = ensure_local_user(
            username=args.username,
            password=args.password,
            display_name=args.display_name,
            is_admin=bool(args.admin),
        )
        print(json.dumps(user.to_public_dict(), ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
