from __future__ import annotations

from typing import Any

from ..config import resolve_domain_config
from .kakuro import KakuroDomainAdapter
from .kenken import KenKenDomainAdapter
from .hitori import HitoriDomainAdapter
from .nonogram import NonogramDomainAdapter
from .starbattle import StarBattleDomainAdapter
from .sudoku import SudokuDomainAdapter


SUPPORTED_DOMAINS = {
    "sudoku": SudokuDomainAdapter,
    "kenken": KenKenDomainAdapter,
    "kakuro": KakuroDomainAdapter,
    "starbattle": StarBattleDomainAdapter,
    "nonogram": NonogramDomainAdapter,
    "hitori": HitoriDomainAdapter,
}


def get_domain_adapter(domain: str, config: dict[str, Any]):
    try:
        adapter_cls = SUPPORTED_DOMAINS[domain]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_DOMAINS))
        raise ValueError(f"Unsupported domain '{domain}'. Supported domains: {supported}.") from exc
    return adapter_cls(resolve_domain_config(config, domain))
