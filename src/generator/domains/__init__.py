from __future__ import annotations

from typing import Any

from ..config import resolve_domain_config
from .chess import ChessDomainAdapter
from .kakuro import KakuroDomainAdapter
from .kenken import KenKenDomainAdapter
from .hitori import HitoriDomainAdapter
from .nonogram import NonogramDomainAdapter
from .nurikabe import NurikabeDomainAdapter
from .othello import OthelloDomainAdapter
from .minesweeper import MinesweeperDomainAdapter
from .wordle import WordleDomainAdapter
from .starbattle import StarBattleDomainAdapter
from .sudoku import SudokuDomainAdapter
from .shikaku import ShikakuDomainAdapter
from .futoshiki import FutoshikiDomainAdapter
from .kakurasu import KakurasuDomainAdapter
from .sumplete import SumpleteDomainAdapter


SUPPORTED_DOMAINS = {
    "chess": ChessDomainAdapter,
    "sudoku": SudokuDomainAdapter,
    "kenken": KenKenDomainAdapter,
    "kakuro": KakuroDomainAdapter,
    "starbattle": StarBattleDomainAdapter,
    "nonogram": NonogramDomainAdapter,
    "hitori": HitoriDomainAdapter,
    "nurikabe": NurikabeDomainAdapter,
    "othello": OthelloDomainAdapter,
    "minesweeper": MinesweeperDomainAdapter,
    "wordle": WordleDomainAdapter,
    "shikaku": ShikakuDomainAdapter,
    "futoshiki": FutoshikiDomainAdapter,
    "kakurasu": KakurasuDomainAdapter,
    "sumplete": SumpleteDomainAdapter,
}


def get_domain_adapter(domain: str, config: dict[str, Any]):
    try:
        adapter_cls = SUPPORTED_DOMAINS[domain]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_DOMAINS))
        raise ValueError(f"Unsupported domain '{domain}'. Supported domains: {supported}.") from exc
    return adapter_cls(resolve_domain_config(config, domain))
