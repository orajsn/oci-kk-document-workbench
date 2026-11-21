"""Start the local demo without inheriting any cloud configuration from the shell."""
from __future__ import annotations

import argparse
from pathlib import Path

from kkpilot.config import Config
from kkpilot.server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "runtime")
    parser.add_argument("--reference-dir", type=Path, help="Optional explicit private reference directory. No AI is run.")
    args = parser.parse_args()
    try:
        config = Config(port=args.port, data_dir=args.data_dir, reference_dir=args.reference_dir)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    serve(config)


if __name__ == "__main__":
    main()
