import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.models import Scenario
from src.generator.nonogram import NonogramPuzzleManager, clues_for_line, parse_puzzle, solve_nonogram, validate_structure


def nonogram_config(tmp_path):
    config = get_config()
    config["domain"] = "nonogram"
    config["output_path"] = str(tmp_path)
    config["output_dir"] = str(tmp_path)
    config["model"] = {"model_name": "test-model"}
    config["similarity"] = {**config["similarity"], **{key: 1.1 for key in ("exact_duplicate_threshold", "normalized_duplicate_threshold", "ngram_overlap_threshold", "embedding_similarity_threshold", "structural_similarity_threshold", "scenario_similarity_threshold", "puzzle_similarity_threshold")}}
    return config


def scenario(edge_case="none", difficulty="easy", task="clue_analysis", tool="line_candidate_scan"):
    return Scenario("nonogram-test", "analyze_line_clues", "single_turn", 1, "intermediate", "analytical", "coaching", "neutral", difficulty, task, edge_case, tool)


def test_line_clues_and_solver_enforce_separated_runs():
    assert clues_for_line([1, 1, 0, 1, 1, 1, 0]) == [2, 3]
    solutions = solve_nonogram(3, 3, [[1], [3], [1]], [[1], [3], [1]], limit=2)
    assert solutions == [[[0, 1, 0], [1, 1, 1], [0, 1, 0]]]


def test_generated_bank_is_deterministic_unique_and_covers_sizes(tmp_path):
    first = NonogramPuzzleManager(resolve_domain_config(nonogram_config(tmp_path), "nonogram"))
    second = NonogramPuzzleManager(resolve_domain_config(nonogram_config(tmp_path), "nonogram"))
    assert [puzzle.puzzle for puzzle in first.base_bank] == [puzzle.puzzle for puzzle in second.base_bank]
    assert [(p.difficulty, p.metadata["height"], p.metadata["width"]) for p in first.base_bank] == [("easy", 5, 5), ("medium", 6, 6), ("hard", 8, 8), ("expert", 10, 10)]
    for puzzle in first.base_bank:
        height, width, rows, columns = parse_puzzle(puzzle.puzzle)
        assert validate_structure(height, width, rows, columns) == []
        assert len(solve_nonogram(height, width, rows, columns, limit=2)) == 1


def test_variants_and_edge_cases_are_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("nonogram", nonogram_config(tmp_path))
    variants = [adapter.select_problem(scenario(), index) for index in range(4)]
    assert [puzzle.transformation for puzzle in variants] == ["identity", "reflect_horizontal", "reflect_vertical", "rotate_90"]
    for puzzle in variants:
        height, width, rows, columns = parse_puzzle(puzzle.puzzle)
        assert len(solve_nonogram(height, width, rows, columns, limit=2)) == 1
    expected = {"invalid_clues": ("invalid", "unknown"), "unsolvable_puzzle": ("valid", "unsolvable"), "ambiguous_puzzle": ("valid", "ambiguous"), "malformed_input": ("malformed", "unknown")}
    for index, (edge_case, statuses) in enumerate(expected.items()):
        selected = scenario(edge_case=edge_case)
        puzzle = adapter.select_problem(selected, index)
        assert (puzzle.ground_truth["validity_status"], puzzle.ground_truth["solvability_status"]) == statuses
        output = {"conversation_type": "single_turn", "category": selected.task_category, "prompt": "Can you inspect these clues?", "response": "I checked the clue runs and crossings.", "board": puzzle.rendered_board}
        assert adapter.validate(output, selected, puzzle).is_valid


class PromptAwareModel:
    def generate(self, prompt):
        category = prompt.split('"task_category": "', 1)[1].split('"', 1)[0]
        return json.dumps({"prompt": "Help me with this Nonogram.", "response": "Start with an overlapping line placement.", "category": category})


def test_profile_tools_prompt_and_end_to_end_generation(tmp_path):
    generator = ConversationGenerator(nonogram_config(tmp_path))
    selected = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected, 0)
    tool = generator.domain.maybe_use_tool(selected, puzzle)
    prompt = generator._build_generation_prompt(selected, puzzle, tool)
    assert "Nonogram" in generator.domain.prompts["system_prompt"]
    assert tool["tool_name"] == "nonogram_line_analysis"
    assert len(prompt) < 10_000
    generator.model_client = PromptAwareModel()
    result = generator.run(samples=1, conversation_type="single_turn", job_name="nonogram-job")
    sample = json.loads((tmp_path / "nonogram-job" / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert result["domain"] == "nonogram"
    assert sample["ground_truth"]["row_evaluations"]
    assert sample["output"]["board"].startswith("Nonogram ")
    assert "row_clues" in (tmp_path / "nonogram-job" / "single_turn.csv").read_text(encoding="utf-8")
