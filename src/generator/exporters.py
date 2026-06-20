from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(_serialize_rows(rows))


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized_rows: list[dict[str, Any]] = []
    for row in rows:
        serialized_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                serialized_row[key] = json.dumps(value, ensure_ascii=False)
            else:
                serialized_row[key] = value
        serialized_rows.append(serialized_row)
    return serialized_rows
