"""Atlas CLI.

atlas run [--headless] [--demo]   start the operations centre
atlas check                       validate config (and later: connectivity)
atlas bundle [--app NAME]         write an AI context bundle (M4)
atlas db <cmd>                    database maintenance (M1)
"""

from __future__ import annotations

import argparse
import sys

from atlas import __version__
from atlas.config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas", description=__doc__)
    parser.add_argument("--version", action="version", version=f"atlas {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the operations centre")
    run.add_argument("--headless", action="store_true", help="collectors + alerts, no TUI")
    run.add_argument("--demo", action="store_true", help="fixture fleet, no SSH or secrets needed")

    sub.add_parser("check", help="validate configuration")
    bundle = sub.add_parser("bundle", help="write an AI context bundle")
    bundle.add_argument("--app", help="limit the bundle to one app")

    args = parser.parse_args(argv)

    match args.command:
        case "run":
            return _cmd_run(headless=args.headless, demo=args.demo)
        case "check":
            return _cmd_check()
        case "bundle":
            print("`atlas bundle` arrives with the AI layer (M4).", file=sys.stderr)
            return 2
        case _:
            parser.print_help()
            return 0


def _cmd_run(*, headless: bool, demo: bool) -> int:
    config = None
    if not demo:
        try:
            config = load_config()
        except ConfigError as e:
            print(f"atlas: {e}", file=sys.stderr)
            return 1
    if headless:
        if config is None:
            print("atlas: --headless needs a config (demo is TUI-only)", file=sys.stderr)
            return 1
        return _run_headless(config)

    from atlas.app import AtlasApp

    AtlasApp(config, demo=demo).run()
    return 0


def _run_headless(config) -> int:
    """Collectors + engine + alerts with no TUI — for testing and servers."""
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    async def _main() -> None:
        from atlas.runtime import Runtime

        runtime = await Runtime.start(config)
        print("atlas: headless mode — Ctrl-C to stop")
        try:
            await asyncio.Event().wait()
        finally:
            await runtime.stop()

    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
    return 0


def _cmd_check() -> int:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"atlas: {e}", file=sys.stderr)
        return 1
    print(f"config ok: {len(config.hosts)} hosts, {len(config.apps)} apps")
    for host in config.hosts:
        kind = "local" if host.is_local else f"ssh {config.ssh.user}@{host.address}"
        print(f"  {host.name:<20} {kind:<28} apps: {', '.join(host.apps) or '—'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
