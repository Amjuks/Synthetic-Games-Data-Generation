import json

from src.generator.config import get_config, resolve_domain_config
from src.generator.domains import get_domain_adapter
from src.generator.generator import ConversationGenerator
from src.generator.models import Scenario
from src.generator.nurikabe import NurikabePuzzleManager, parse_puzzle, solution_violations, solve_nurikabe, validate_structure


def nurikabe_config(tmp_path):
    config = get_config(); config["domain"] = "nurikabe"; config["output_path"] = str(tmp_path); config["output_dir"] = str(tmp_path); config["model"] = {"model_name": "test-model"}
    config["similarity"] = {**config["similarity"], **{key: 1.1 for key in ("exact_duplicate_threshold", "normalized_duplicate_threshold", "ngram_overlap_threshold", "embedding_similarity_threshold", "structural_similarity_threshold", "scenario_similarity_threshold", "puzzle_similarity_threshold")}}
    return config


def scenario(edge_case="none", difficulty="easy", task="island_analysis", tool="deduction_scan"):
    return Scenario("nurikabe-test", "analyze_island_growth", "single_turn", 1, "intermediate", "analytical", "coaching", "neutral", difficulty, task, edge_case, tool)


def test_solver_enforces_all_nurikabe_rules():
    clues = [[0, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 2], [0, 3, 0, 0]]
    solutions = solve_nurikabe(4, clues, limit=2)
    assert len(solutions) == 1
    assert solution_violations(4, clues, solutions[0]) == []


def test_generated_bank_is_deterministic_unique_and_covers_difficulties(tmp_path):
    first = NurikabePuzzleManager(resolve_domain_config(nurikabe_config(tmp_path), "nurikabe"))
    second = NurikabePuzzleManager(resolve_domain_config(nurikabe_config(tmp_path), "nurikabe"))
    assert [puzzle.puzzle for puzzle in first.base_bank] == [puzzle.puzzle for puzzle in second.base_bank]
    assert [puzzle.difficulty for puzzle in first.base_bank] == ["easy", "medium", "hard", "expert"]
    for puzzle in first.base_bank:
        size, clues = parse_puzzle(puzzle.puzzle)
        assert validate_structure(size, clues) == []
        assert len(solve_nurikabe(size, clues, limit=2)) == 1


def test_variants_and_edge_cases_are_solver_confirmed(tmp_path):
    adapter = get_domain_adapter("nurikabe", nurikabe_config(tmp_path))
    variants = [adapter.select_problem(scenario(), index) for index in range(4)]
    assert [puzzle.transformation for puzzle in variants] == ["identity", "reflect_horizontal", "reflect_vertical", "rotate_90"]
    for puzzle in variants:
        size, clues = parse_puzzle(puzzle.puzzle)
        assert len(solve_nurikabe(size, clues, 2)) == 1
    expected = {"invalid_grid": ("invalid", "unknown"), "unsolvable_puzzle": ("valid", "unsolvable"), "ambiguous_puzzle": ("valid", "ambiguous"), "malformed_input": ("malformed", "unknown")}
    for index, (edge_case, statuses) in enumerate(expected.items()):
        selected = scenario(edge_case=edge_case); puzzle = adapter.select_problem(selected, index)
        assert (puzzle.ground_truth["validity_status"], puzzle.ground_truth["solvability_status"]) == statuses
        output = {"conversation_type": "single_turn", "category": selected.task_category, "prompt": "Can you inspect this Nurikabe?", "response": "I checked island size, sea connectivity, and 2x2 sea constraints.", "board": puzzle.rendered_board}
        assert adapter.validate(output, selected, puzzle).is_valid


class PromptAwareModel:
    def generate(self, prompt):
        category = prompt.split('"task_category": "', 1)[1].split('"', 1)[0]
        return json.dumps({"prompt": "Help me with this Nurikabe.", "response": "Start with the island whose clue fixes a sea boundary.", "category": category})


def test_profile_tools_prompt_and_end_to_end_generation(tmp_path):
    generator = ConversationGenerator(nurikabe_config(tmp_path)); selected = scenario(difficulty="expert")
    puzzle = generator.domain.select_problem(selected, 0); tool = generator.domain.maybe_use_tool(selected, puzzle); prompt = generator._build_generation_prompt(selected, puzzle, tool)
    assert "Nurikabe" in generator.domain.prompts["system_prompt"]
    assert tool["tool_name"] == "nurikabe_deduction_scan"
    assert len(prompt) < 10_000
    generator.model_client = PromptAwareModel(); result = generator.run(samples=1, conversation_type="single_turn", job_name="nurikabe-job")
    sample = json.loads((tmp_path / "nurikabe-job" / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert result["domain"] == "nurikabe"
    assert sample["ground_truth"]["solution_violations"] == []
    assert sample["output"]["board"].startswith("Nurikabe ")
    assert "grid_size" in (tmp_path / "nurikabe-job" / "single_turn.csv").read_text(encoding="utf-8")
