from __future__ import annotations

import json

from .chess import ChessToolkit
from .config import get_config, resolve_domain_config


def main() -> None:
    config = get_config()
    config["domain"] = "chess"
    result = ChessToolkit(resolve_domain_config(config, "chess")).verify_backends()
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
