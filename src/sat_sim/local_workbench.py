"""Local desktop entrypoint; the shared API retains its opt-in default."""
from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import MutableMapping

from .interactive import FEATURE_FLAG_ENV


def configure_local_environment(
    host: str, environ: MutableMapping[str, str] | None = None,
) -> None:
    """Enable interactive discovery only for an unspecified loopback setup.

    Explicit false/empty values and supervised execution policy are untouched.
    Hostnames are never DNS-resolved: a remote host must not become implicitly
    trusted because of a transient resolver result.
    """
    env = os.environ if environ is None else environ
    if FEATURE_FLAG_ENV in env:
        return
    host = host.strip().lower()
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if loopback:
        env[FEATURE_FLAG_ENV] = "1"


def main(argv: list[str] | None = None) -> int:
    """Use the original CLI parser and serve handler without another server."""
    from .agent_cli import build_parser

    args = build_parser().parse_args(["serve", *(sys.argv[1:] if argv is None else argv)])
    configure_local_environment(args.host)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
