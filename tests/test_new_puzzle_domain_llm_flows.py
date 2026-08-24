from __future__ import annotations

import csv
import json
import re

import pytest

from src.generator.config import get_config
from src.generator.generator import ConversationGenerator


DOMAIN_PROFILES = {
    "shikaku": ("rectangle_analysis", "rectangle_candidate_scan", "candidate_rectangles"),
    "futoshiki": ("inequality_analysis", "inequality_scan", "propagated_candidates"),
    "kakurasu": ("weighted_sum_analysis", "weighted_sum_scan", "row_patterns"),
    "sumplete": ("subset_analysis", "subset_sum_scan", "row_subsets"),
}


class ModeAwareModel:
    def generate(self, prompt: str) -> str:
        category_match = re.search(r'"task_category":\s*"([^"]+)"', prompt)
        assert category_match is not None
        category = category_match.group(1)
        if '"conversation_type": "multi_turn"' in prompt:
            return json.dumps(
                {
                    "messages": [
                        {
                            "user": f"Show me the first verified {category} deduction.",
                            "response": "Start with the most constrained line or clue from the tool evidence.",
                        },
                        {
                            "user": "How does the crossing constraint confirm it?",
                            "response": "Compare the remaining candidates against the intersecting constraint.",
                        },
                    ]
                }
            )
        return json.dumps(
            {
                "prompt": f"Help me with this verified {category} step.",
                "response": "Use the smallest candidate set and confirm it against its crossing constraint.",
            }
        )


@pytest.mark.parametrize("domain", DOMAIN_PROFILES)
def test_new_domains_complete_mocked_llm_jobs_in_every_conversation_mode(
    tmp_path, domain
):
    category, tool_mode, complete_evidence_key = DOMAIN_PROFILES[domain]
    config = get_config()
    config["domain"] = domain
    config["output_path"] = str(tmp_path)
    config["output_dir"] = str(tmp_path)
    config["max_turns"] = 3
    config["generation"]["max_regeneration_attempts"] = 2
    config["similarity"].update(
        {
            "exact_duplicate_threshold": 1.1,
            "normalized_duplicate_threshold": 1.1,
            "ngram_overlap_threshold": 1.1,
            "embedding_similarity_threshold": 1.1,
            "structural_similarity_threshold": 1.1,
            "scenario_similarity_threshold": 1.1,
            "puzzle_similarity_threshold": 1.1,
        }
    )
    config["domains"][domain]["scenario"].update(
        {
            "task_categories": [category],
            "difficulty_levels": ["easy"],
            "edge_cases": ["none"],
            "tool_usage_modes": [tool_mode],
        }
    )
    generator = ConversationGenerator(config)
    generator.model_client = ModeAwareModel()

    for mode in ("single_turn", "multi_turn", "both"):
        result = generator.run(
            samples=1,
            conversation_type=mode,
            max_turns=3,
            job_name=f"{domain}-{mode}",
        )
        job_dir = tmp_path / f"{domain}-{mode}"
        samples = [
            json.loads(line)
            for line in (job_dir / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        expected_types = (
            ["single_turn", "multi_turn"] if mode == "both" else [mode]
        )

        assert result["domain"] == domain
        assert result["completed"] == 1
        assert result["accepted_samples"] == len(expected_types)
        assert [sample["conversation_type"] for sample in samples] == expected_types
        assert all(sample["domain"] == domain for sample in samples)
        assert all(sample["task"] == category for sample in samples)
        assert all(sample["output"]["category"] == category for sample in samples)
        assert all(
            sample["output"]["board"]
            == sample["puzzle_metadata"]["rendered_board"]
            for sample in samples
        )
        assert all(sample["ground_truth"][complete_evidence_key] for sample in samples)
        assert all(sample["ground_truth"]["solution_violations"] == [] for sample in samples)
        assert all(
            2 <= len(sample["tool_usage_details"]["calls"]) <= 5
            for sample in samples
        )
        assert all(
            call["output"]["verified"]
            for sample in samples
            for call in sample["tool_usage_details"]["calls"]
        )

        progress = json.loads((job_dir / "progress.json").read_text(encoding="utf-8"))
        assert progress["status"] == "completed"
        assert progress["completed"] == 1
        assert progress["accepted_samples"] == len(expected_types)

        for conversation_type in expected_types:
            csv_path = job_dir / f"{conversation_type}.csv"
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            matching_sample = next(
                sample
                for sample in samples
                if sample["conversation_type"] == conversation_type
            )
            assert len(csv_rows) == 1
            assert csv_rows[0]["domain"] == domain
            assert csv_rows[0]["board"] == matching_sample["output"]["board"]
            assert int(csv_rows[0]["tool_count"]) >= 2
