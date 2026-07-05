from __future__ import annotations

from typing import Any

from .sudoku import SudokuDomainAdapter


SUPPORTED_DOMAINS = {
    "sudoku": SudokuDomainAdapter,
}


def get_domain_adapter(domain: str, config: dict[str, Any]):
    try:
        adapter_cls = SUPPORTED_DOMAINS[domain]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_DOMAINS))
        raise ValueError(f"Unsupported domain '{domain}'. Supported domains: {supported}.") from exc
    return adapter_cls(config)
