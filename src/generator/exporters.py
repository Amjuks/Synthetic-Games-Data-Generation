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


def append_csv_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_row = _serialize_row(row)
    fieldnames = list(serialized_row.keys())
    file_exists = path.exists()
    should_write_header = not file_exists or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if should_write_header:
            writer.writeheader()
        writer.writerow(serialized_row)


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized_rows: list[dict[str, Any]] = []
    for row in rows:
        serialized_rows.append(_serialize_row(row))
    return serialized_rows


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    serialized_row: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            serialized_row[key] = json.dumps(value, ensure_ascii=False)
        else:
            serialized_row[key] = value
    return serialized_row
