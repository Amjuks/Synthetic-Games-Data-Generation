import csv

from src.generator.exporters import append_csv_row


def test_append_csv_row_expands_header_for_changed_domain_fields(tmp_path):
    path = tmp_path / "samples.csv"
    append_csv_row(path, {"sample_id": "sudoku-1", "domain": "sudoku", "board": "board"})
    append_csv_row(
        path,
        {
            "sample_id": "kenken-1",
            "domain": "kenken",
            "board": "board",
            "grid_size": 4,
            "cages": [{"id": "C1"}],
        },
    )

    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert rows[0]["sample_id"] == "sudoku-1"
    assert rows[0]["grid_size"] == ""
    assert rows[1]["sample_id"] == "kenken-1"
    assert rows[1]["grid_size"] == "4"
    assert rows[1]["cages"] == '[{"id": "C1"}]'
