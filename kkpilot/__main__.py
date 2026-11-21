"""Run with: python -m kkpilot serve --port 8765."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import from_environment
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback-only KK pilot; demo/reference replay is not AI extraction.")
    parser.add_argument("command", nargs="?", choices=("serve",), default="serve")
    parser.add_argument("--host", help="Only 127.0.0.1 is allowed.")
    parser.add_argument("--port", type=int)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--reference-dir", type=Path, help="Explicit directory containing local reference replay records.")
    parser.add_argument("--oci-config", type=Path, help="Explicit private JSON approval configuration; live mode remains disabled without it.")
    arguments = parser.parse_args()
    try:
        config = from_environment(host=arguments.host, port=arguments.port, workers=arguments.workers,
                                  data_dir=arguments.data_dir, reference_dir=arguments.reference_dir, oci_config_path=arguments.oci_config)
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    serve(config)


if __name__ == "__main__":
    main()
