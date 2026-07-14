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
    if not path.exists() or path.stat().st_size == 0:
        fieldnames = list(serialized_row.keys())
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(serialized_row)
        return

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fieldnames = list(reader.fieldnames or [])
        existing_rows = list(reader)

    new_fieldnames = [key for key in serialized_row if key not in existing_fieldnames]
    fieldnames = existing_fieldnames + new_fieldnames
    if new_fieldnames:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerow(serialized_row)
        return

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
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
