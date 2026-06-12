"""Command-line entry point for the Yokogawa LAN service."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _default_config() -> str:
    return os.environ.get("YOKOGAWA_SERVICE_CONFIG", "site/yokogawa.yaml")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="yoko-lan")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the Yokogawa HTTP service.")
    serve.add_argument("--config", default=_default_config())
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8020)
    serve.add_argument("--reload", action="store_true")
    access = serve.add_mutually_exclusive_group()
    access.add_argument("--access-log", dest="access_log", action="store_true")
    access.add_argument("--no-access-log", dest="access_log", action="store_false")
    serve.set_defaults(access_log=None)

    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        config_path = str(Path(args.config).resolve())
        os.environ["YOKOGAWA_SERVICE_CONFIG"] = config_path
        app = (
            "yokogawa_lan_service.cli:_app_factory"
            if args.reload
            else _app_factory()
        )
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            reload=args.reload,
            factory=args.reload,
            access_log=args.access_log if args.access_log is not None else True,
            log_level="info",
            reload_dirs=[str(Path.cwd())] if args.reload else None,
        )


def _app_factory():
    from yokogawa_lan_service.api import create_app

    config_path = os.environ.get("YOKOGAWA_SERVICE_CONFIG", "site/yokogawa.yaml")
    return create_app(config_path)


if __name__ == "__main__":  # pragma: no cover
    main()
