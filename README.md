# Synthetic Puzzle Conversation Data Generator

## Project Overview
This project generates synthetic Chess, Sudoku, KenKen, Kakuro, Star Battle, Nonogram (Picross/Griddlers), Hitori, Nurikabe, Othello, Minesweeper, and Wordle chat datasets with persistent job state, structured scenario generation, puzzle selection, validation, and diversity checks.

Primary use case:
- Build large puzzle conversation datasets for training, evaluation, or experimentation.

Key features:
- Generates `single_turn`, `multi_turn`, or both conversation types in one run.
- Supports `sudoku`, solver-backed `kenken`, solver-backed `kakuro`, solver-backed `starbattle`, and solver-backed `nonogram` through a domain adapter layer.
- Supports solver-backed `hitori` and `nurikabe` through the same domain adapter layer.
- Supports local deterministic `othello`, exact-frontier `minesweeper`, and offline classic-vocabulary `wordle` engines through the same adapter contract.
- Supports stateful `chess` conversations from validated PGN/SAN game histories or complete FEN positions.
- Uses structured scenarios instead of relying only on LLM creativity.
- Selects puzzles from a persistent puzzle bank and creates transformed or edge-case variants.
- Generates deterministic, domain-specific ground-truth metadata for each puzzle.
- Tracks tool usage explicitly for each generated or rejected sample.
- Validates generated outputs before accepting them.
- Rejects overly similar samples and stores rejected attempts for inspection.
- Writes outputs incrementally and can resume interrupted jobs.
- Allows a named job to resume with different domain, conversation-type, and turn-limit settings; changes are recorded in progress and logs.
- Writes readable and structured per-job model request/error logs, including the exact credential-free request payload.
- Supports `openai`, `custom_chat`, `tensorstudio`, and mock generation modes.

## Setup Instructions
### Prerequisites
- Python `>=3.10`
- A virtual environment is recommended
- One of the following model backends:
  - OpenAI API
  - A compatible custom chat-completions endpoint
  - TensorStudio chat-completions API
  - No external API for mock generation

### Dependencies
From [requirements.txt](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/requirements.txt):

```text
PyYAML>=6.0
python-dotenv>=1.0.0
openai>=1.0.0
requests>=2.31.0
pydantic>=2.0.0
pytest>=8.0.0
```

### Installation
1. Create and activate a virtual environment.
2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Create `.env` from `.env.example`.

```bash
copy .env.example .env
```

4. Fill in the environment variables for your chosen provider.

### Environment Variables
The project loads `.env` automatically from the repository root.

#### Common provider switch

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `MODEL_PROVIDER` | Selects the model backend. | string | `openai` | `openai`, `custom_chat`, `tensorstudio` | Optional |

#### OpenAI settings

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key. | string | none | any valid key | Required for `openai` |
| `OPENAI_MODEL` | OpenAI model name. | string | `gpt-4o-mini` | any model accepted by the SDK | Optional |
| `OPENAI_BASE_URL` | Override base URL for the OpenAI client. | string | none | valid URL | Optional |
| `OPENAI_TEMPERATURE` | Sampling temperature. | float | `0.8` | provider-dependent | Optional |
| `OPENAI_MAX_TOKENS` | Maximum output tokens. | int | `800` | positive integers | Optional |
| `OPENAI_TIMEOUT` | Request timeout in seconds. | float | `60` | positive numbers | Optional |

#### Custom chat-completions settings

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `CUSTOM_API_URL` | Chat-completions endpoint URL. | string | none | valid URL | Required for `custom_chat` |
| `CUSTOM_API_KEY` | Bearer token for the custom endpoint. | string | none | any valid token | Optional unless endpoint requires auth |
| `CUSTOM_MODEL` | Model name sent to the custom endpoint. | string | `gpt-oss-120b` | any endpoint-supported model | Optional |
| `CUSTOM_TEMPERATURE` | Sampling temperature. | float | `0.1` | endpoint-dependent | Optional |
| `CUSTOM_MAX_TOKENS` | Maximum tokens sent as `max_tokens`. | int | `800` | positive integers | Optional |
| `CUSTOM_TIMEOUT` | Request timeout in seconds. | float | `60` | positive numbers | Optional |
| `CUSTOM_REASONING` | Included in the system message as `Reasoning: ...`. | string | `Low` | any string | Optional |
| `CUSTOM_ENABLE_THINKING` | Sent under `chat_template_kwargs.enable_thinking`. | bool | `false` | `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off` | Optional |

#### TensorStudio chat-completions settings

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `TENSORSTUDIO_API_URL` | TensorStudio chat-completions endpoint URL. | string | `https://api.tensorstudio.ai/v1/chat/completions` | valid URL | Optional |
| `TENSORSTUDIO_API_KEY` | Bearer token for TensorStudio. | string | none | any valid token | Required for `tensorstudio` |
| `TENSORSTUDIO_MODEL` | Model name sent to TensorStudio. | string | `glm-5.2-fp8` | any TensorStudio-supported model | Optional |
| `TENSORSTUDIO_TEMPERATURE` | Sampling temperature. | float | `0.8` | provider-dependent | Optional |
| `TENSORSTUDIO_MAX_TOKENS` | Maximum tokens sent as `max_tokens`. | int | `800` | positive integers | Optional |
| `TENSORSTUDIO_TIMEOUT` | Request timeout in seconds. TensorStudio can be slower for large generations. | float | `300` | positive numbers | Optional |
| `TENSORSTUDIO_SESSION_ID` | Optional value sent as `metadata.session_id`. | string | none | any string | Optional |

### Configuration Files
- [config/defaults.yaml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/config/defaults.yaml): shared runtime defaults and domain profiles for generation, scenarios, puzzles, and storage.
- [config/prompts.yaml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/config/prompts.yaml): prompt profiles for each supported puzzle domain.

No additional external datasets are required by the current code. The current implementation uses a built-in puzzle bank and writes a persistent copy to the output directory.

## Usage
### Run the CLI

```bash
python -m src.generator.cli --help
python -m src.generator.cli run --domain sudoku --samples 5
python -m src.generator.cli run --domain kenken --samples 5
python -m src.generator.cli run --domain kakuro --samples 5
python -m src.generator.cli run --domain starbattle --samples 5
python -m src.generator.cli run --domain nonogram --samples 5
python -m src.generator.cli run --domain hitori --samples 5
python -m src.generator.cli run --domain nurikabe --samples 5
python -m src.generator.cli run --domain othello --samples 5
python -m src.generator.cli run --domain minesweeper --samples 5
python -m src.generator.cli run --domain wordle --samples 5
python -m src.generator.cli status --job-name my_job
```

The project also exposes a console script from [pyproject.toml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/pyproject.toml):

```bash
sudoku-generator run --domain sudoku --samples 5
puzzle-generator run --domain kenken --samples 5
puzzle-generator run --domain kakuro --samples 5
puzzle-generator run --domain starbattle --samples 5
puzzle-generator run --domain nonogram --samples 5
puzzle-generator run --domain hitori --samples 5
puzzle-generator run --domain nurikabe --samples 5
puzzle-generator run --domain othello --samples 5
puzzle-generator run --domain minesweeper --samples 5
puzzle-generator run --domain wordle --samples 5
```

### Commands

| Command | Description |
|---|---|
| `run` | Starts or resumes a generation job. |
| `status` | Prints `progress.json` for a job directory or job name. |

### CLI Arguments
#### `run`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `--samples` | Total sample indexes to process. On resume, the new value replaces the previous target but cannot be lower than the number already completed. | int | `config.defaults.samples` -> `10` | positive integers | Optional |
| `--domain` | Generation domain. | string | `config.defaults.domain` -> `sudoku` | `chess`, `sudoku`, `kenken`, `kakuro`, `starbattle`, `nonogram`, `hitori`, `nurikabe`, `othello`, `minesweeper`, `wordle` | Optional |
| `--conversation-type` | Which conversation types to generate. | string | `config.defaults.conversation_type` -> `both` | `single_turn`, `multi_turn`, `both` | Optional |
| `--max-turns` | Upper bound for generated multi-turn scenario length. | int | `config.defaults.max_turns` -> `6` | positive integers | Optional |
| `--job-name` | Output job directory name. If omitted, a timestamp-based name is generated. | string | auto-generated | any filesystem-safe string | Optional |

#### `status`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `--job-name` | Looks for `<output_path>/<job_name>/progress.json`. | string | none | existing job names | Optional |
| `--job-dir` | Reads progress from an explicit job directory. | string/path | none | valid directory path | Optional |

Notes:
- For `status`, either `--job-name` or `--job-dir` should be provided.
- The code prints a help message if neither is supplied.

### Configuration Options
The following sections are read from [config/defaults.yaml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/config/defaults.yaml).

#### Top-level defaults

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `samples` | Default sample count for `run`. | int | `10` | positive integers | Optional |
| `domain` | Default generation domain. | string | `sudoku` | `sudoku` | Optional |
| `conversation_type` | Default conversation type mode. | string | `both` | `single_turn`, `multi_turn`, `both` | Optional |
| `max_turns` | Default multi-turn upper bound. | int | `6` | positive integers | Optional |
| `output_dir` | Output directory relative to repository root. | string | `outputs` | valid directory names | Optional |
| `job_name` | Present in config but not currently consumed by the CLI run path. | null/string | `null` | any string | Optional |

#### `generation`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `random_seed` | Base seed for scenario and puzzle variation. | int | `17` | integers | Optional |
| `max_regeneration_attempts` | Maximum attempts per sample variant before raising an error. | int | `3` | positive integers | Optional |

#### `model`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `provider` | Default provider before env overrides. | string | `openai` | backend-specific | Optional |
| `model_name` | Default model name before env overrides. | string | `gpt-4o-mini` | backend-specific | Optional |
| `temperature` | Default temperature before env overrides. | float | `0.8` | backend-specific | Optional |
| `max_tokens` | Default token limit before env overrides. | int | `800` | positive integers | Optional |
| `timeout` | Default request timeout in seconds before provider-specific env overrides. | float | `60` | positive numbers | Optional |

#### `storage`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `metadata_filename` | JSONL file for accepted sample metadata. | string | `samples.jsonl` | valid filenames | Optional |
| `rejected_filename` | JSONL file for rejected sample records. | string | `rejected_samples.jsonl` | valid filenames | Optional |
| `stats_filename` | JSON file for aggregate dataset stats. | string | `dataset_stats.json` | valid filenames | Optional |

#### `puzzles`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `persistent_bank_filename` | Output-side persistent puzzle bank snapshot. | string | `puzzle_bank.jsonl` | valid filenames | Optional |
| `usage_stats_filename` | JSON file tracking puzzle usage counts. | string | `puzzle_usage.json` | valid filenames | Optional |

#### `similarity`

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `max_history` | Maximum accepted-history window used for similarity checks. | int | `5000` | positive integers | Optional |
| `ngram_size` | N-gram size for overlap calculations. | int | `3` | positive integers | Optional |
| `exact_duplicate_threshold` | Threshold for exact text duplicates. | float | `1.0` | `0.0` to `1.0` | Optional |
| `normalized_duplicate_threshold` | Threshold for whitespace/lowercase normalized duplicates. | float | `1.0` | `0.0` to `1.0` | Optional |
| `ngram_overlap_threshold` | Threshold for n-gram Jaccard similarity. | float | `0.92` | `0.0` to `1.0` | Optional |
| `embedding_similarity_threshold` | Threshold for token-vector cosine similarity. | float | `0.96` | `0.0` to `1.0` | Optional |
| `structural_similarity_threshold` | Threshold for structural similarity. | float | `0.97` | `0.0` to `1.0` | Optional |
| `scenario_similarity_threshold` | Threshold for scenario field overlap. | float | `0.95` | `0.0` to `1.0` | Optional |
| `puzzle_similarity_threshold` | Threshold for exact puzzle identity. | float | `1.0` | `0.0` to `1.0` | Optional |
| `related_puzzle_similarity` | Score assigned to related transformed puzzles with the same parent/canonical base. | float | `0.35` | `0.0` to `1.0` | Optional |
| `related_edge_case_puzzle_similarity` | Score assigned to related edge-case puzzle variants sharing the same parent and edge-case kind. | float | `0.55` | `0.0` to `1.0` | Optional |

#### `scenario`

These values define the candidate pools used by the scenario generator.

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `task_categories` | Candidate task categories. | list[string] | see file | any strings | Optional |
| `difficulty_levels` | Candidate difficulty labels. | list[string] | `easy`, `medium`, `hard`, `expert` | any strings | Optional |
| `user_expertise_levels` | Candidate expertise labels. | list[string] | `beginner`, `intermediate`, `advanced`, `expert` | any strings | Optional |
| `user_personalities` | Candidate user personalities. | list[string] | see file | any strings | Optional |
| `assistant_styles` | Candidate assistant styles. | list[string] | see file | any strings | Optional |
| `tones` | Candidate tones. | list[string] | see file | any strings | Optional |
| `edge_cases` | Candidate edge-case modes. | list[string] | `none`, `incorrect_assumption`, `invalid_board`, `ambiguous_board`, `unsolvable_board`, `malformed_input` | any strings | Optional |
| `tool_usage_modes` | Candidate tool-usage descriptors. | list[string] | `none`, `candidate_scan`, `row_column_box_check`, `solution_verification` | any strings | Optional |
| `puzzles.min_tool_catalog_size` | Minimum registered tools required for every domain adapter. | integer | `10` | `>= 1` | Optional |

### Example Commands
#### Basic single run

```bash
python -m src.generator.cli run --domain sudoku --samples 5
```

Uses the defaults from `config/defaults.yaml` and generates both conversation types unless overridden.

#### Single-turn only

```bash
python -m src.generator.cli run --domain sudoku --samples 100 --conversation-type single_turn --job-name sudoku_single_100
```

Creates only single-turn samples and stores them under `outputs/sudoku_single_100/`.

#### Multi-turn focused run

```bash
python -m src.generator.cli run --domain sudoku --samples 50 --conversation-type multi_turn --max-turns 8 --job-name multi_coaching
```

Useful when you want only multi-turn data and want scenarios to be allowed up to eight exchanges.

#### Run with TensorStudio

Set these values in `.env`:

```dotenv
MODEL_PROVIDER=tensorstudio
TENSORSTUDIO_API_URL=https://api.tensorstudio.ai/v1/chat/completions
TENSORSTUDIO_API_KEY=your_tensorstudio_api_key_here
TENSORSTUDIO_MODEL=glm-5.2-fp8
TENSORSTUDIO_MAX_TOKENS=800
TENSORSTUDIO_TIMEOUT=300
TENSORSTUDIO_SESSION_ID=sudoku-generation
```

Then run:

```bash
python -m src.generator.cli run --domain sudoku --samples 10 --job-name tensorstudio_test
```

Use this when you want to generate through TensorStudio's OpenAI-style chat-completions endpoint instead of the OpenAI Responses API or the custom local chat endpoint.

#### Resume an interrupted job

```bash
python -m src.generator.cli run --domain sudoku --samples 500 --conversation-type both --job-name my_large_job
```

If `outputs/my_large_job/progress.json` already exists, the generator resumes from `next_sample_index`.

#### Inspect job status by name

```bash
python -m src.generator.cli status --job-name my_large_job
```

Prints the current progress JSON.

#### Inspect job status by explicit path

```bash
python -m src.generator.cli status --job-dir outputs/my_large_job
```

Useful when reading a copied or moved job directory.

## Generated Outputs
Each run writes to a job directory:

```text
outputs/<job_name>/
```

Example:

```text
outputs/job_20260629_140641_6ca5/
```

### Directory Structure

| Path | Format | Purpose |
|---|---|---|
| `outputs/<job_name>/progress.json` | JSON | Job state, resume position, counters, and last error details. |
| `outputs/<job_name>/samples.jsonl` | JSONL | Accepted samples with full metadata. |
| `outputs/<job_name>/rejected_samples.jsonl` | JSONL | Rejected attempts and rejection reasons. |
| `outputs/<job_name>/dataset_stats.json` | JSON | Aggregate accepted-sample distribution stats. |
| `outputs/<job_name>/single_turn.csv` | CSV | Flattened accepted single-turn rows. |
| `outputs/<job_name>/multi_turn.csv` | CSV | Flattened accepted multi-turn rows. |
| `outputs/puzzle_bank.jsonl` or `<output_path>/puzzle_bank.jsonl` | JSONL | Persistent snapshot of the built-in puzzle bank. |
| `outputs/puzzle_usage.json` or `<output_path>/puzzle_usage.json` | JSON | Puzzle and parent-puzzle usage counts. |
| `<job_dir>/generation.log` | text | Readable request, response, error, and traceback history. |
| `<job_dir>/generation_events.jsonl` | JSONL | Structured job and model-call events, including full credential-free request payloads. |

### File Details
#### `progress.json`
Contains:
- Job identifiers
- `status`
- `completed`
- `total`
- `next_sample_index`
- `domain`
- `accepted_samples`
- `rejected_samples`
- `distribution_stats`
- output file paths
- `last_error` and traceback when failures occur

#### `samples.jsonl`
Each line is one accepted sample with fields such as:
- `sample_id`
- `domain`
- `scenario_id`
- `puzzle_id`
- `parent_puzzle_id`
- `conversation_type`
- `task`
- `difficulty`
- `turns`
- `similarity_score`
- `generation_model`
- `timestamp`
- `validation_status`
- `scenario`
- `puzzle_metadata`
- `ground_truth`
- `tool_used`
- `tool_usage_details`
- `conversation`
- `output`

#### `rejected_samples.jsonl`
Each line stores a rejected generation attempt with:
- `sample_index`
- `attempt`
- `domain`
- `rejection_type`
- `reasons`
- `metrics`
- `scenario`
- `puzzle_metadata`
- `ground_truth`
- `tool_used`
- `tool_usage_details`
- `output`

#### `dataset_stats.json`
Tracks accepted-sample aggregate distributions, including:
- `accepted_samples`
- `task_distribution`
- `difficulty_distribution`
- `user_expertise_distribution`
- `assistant_style_distribution`
- `tone_distribution`
- `edge_case_distribution`
- `tool_usage_distribution`
- `tool_used_distribution`
- `tool_name_distribution`
- `domain_distribution`
- `puzzle_reuse_distribution`
- `conversation_type_distribution`
- `conversation_length_distribution`

#### `single_turn.csv`
Flattened fields include:
- identifiers and metadata columns
- `domain`
- `tool_used`
- `tool_name`
- `prompt`
- `response`
- `board`

#### `multi_turn.csv`
Flattened fields include:
- identifiers and metadata columns
- `domain`
- `tool_used`
- `tool_name`
- `messages` as serialized JSON
- `board`

## Domain Extension Structure
The shared pipeline is orchestrated by `ConversationGenerator`, while domain behavior is selected through `src/generator/domains`.

Current supported domain:
- `chess`
- `sudoku`
- `kenken`
- `kakuro`
- `starbattle`
- `nonogram`
- `hitori`
- `nurikabe`
- `othello`
- `minesweeper`
- `wordle`

Each adapter owns domain-specific scenario generation, puzzle selection, tool decisions, validation, prompt context, prompts, and CSV row extensions. Unsupported domains fail with a clear error before generation starts.

To add a future domain, add a new adapter under `src/generator/domains/` and register it in `src/generator/domains/__init__.py`.

## Tool Usage And Ground Truth
Each accepted and rejected sample records whether a domain tool was used.

Tool metadata includes:
- `tool_used`
- `tool_usage_details.used`
- `tool_usage_details.tool_name`
- `tool_usage_details.tool_input`
- `tool_usage_details.tool_output`
- `tool_usage_details.reason`

Chess requires at least two successful, purposeful, distinct checks for every record. It preserves the existing primary-tool fields and also records ordered calls in `tool_usage_details.calls`; CSV exports add `tool_count`, `tool_names`, serialized `tool_calls`, and a stable tool-bundle signature. Difficulty is derived from the measured position complexity, and the deterministic record schedule cycles through easy, medium, hard, and expert targets so normal-sized jobs cover the full range. Each Chess sample explicitly records `input_type`, `conversation_type`, `category`, `difficulty`, `side_to_move`, `notation_format`, `tools_required`, `tools_used`, `verification_status`, `lineage_id`, `dataset_split`, position phase, measured complexity and complexity band, motif/theme, canonical state signature, edge mutation type, and catalog version.

The versioned `chess-v2` catalog contains at least 256 deterministic legal game lineages with up to four materially different snapshots per lineage, plus dedicated special-position families. A canonical signature over the first four FEN fields prevents the same board from appearing twice in a job, even through different PGN/FEN representations; input signatures prevent exact prompt reuse, and accepted/rejected history is reserved across retries and resumes. All descendants of one lineage receive the same stable train/validation/test split to prevent cross-split near-duplicate leakage. The deterministic scheduler assigns exactly three compatible edge cases per rolling 20 records and derives each mutation from a different valid base position. Complete FEN strings retain side to move, castling rights, en passant target, halfmove clock, and fullmove number. The Chess toolkit parses FEN/PGN/SAN/UCI, reconstructs histories, lists and applies legal moves, converts and undoes moves, diagnoses malformed or contradictory inputs, analyzes material and positional features, detects terminal/draw states (including history-dependent repetition), inspects attacks/defenders/pins/checks/captures, records exact multi-turn FEN traces, and supports engine and tablebase verification.

Chess verification is operational by default: `engine_path: auto` and `tablebase_path: auto`. On the first engine-backed request, the pipeline discovers a configured/PATH engine or downloads the platform-appropriate stable Stockfish asset from the official GitHub release, verifies the release-provided SHA-256, extracts it safely into `outputs/.chess_backends/stockfish`, completes a UCI identity/legal-move healthcheck, and reuses the cache. Set `CHESS_ENGINE_PATH` only to override this with another local Stockfish executable. For ≤7-piece positions, a local `CHESS_TABLEBASE_PATH` takes priority; otherwise `auto` uses the retrying Lichess Syzygy endpoint enabled by default. `verification_backends_required: true` makes engine/tablebase categories fail before the model call if their required evidence cannot be verified—unverified best-move, tactical, evaluation, and tablebase samples are not generated.

Every adapter exposes an immutable, inspectable `tool_catalog`. Startup validates the configured minimum size, unique domain-prefixed names, callable executors, registered routes, and reachability from configured scenarios. Sudoku, KenKen, Kakuro, Star Battle, Nonogram, Hitori, Nurikabe, and Othello register 10 tools; Minesweeper registers 12, Wordle 13, and Chess 20. Every generated record still uses only a purposeful subset of normally two to five distinct verified calls. Their schedulers cycle through easy, medium, hard, and expert positions; each puzzle records a deterministic complexity score, band, and measurable factors such as dimensions, clue density, and strategy count. Puzzle IDs from accepted and rejected history are reserved on resume, and selection refuses to reuse a puzzle within a job.

Hints and next-move scenarios route to focused candidates, deductions, and move-impact evidence. Technique and advanced scenarios use unit, intersection, quota, pattern, connectivity, or capacity analyzers. Validity and edge scenarios use deterministic diagnostics. Solver-space tools use a two-solution cap and expose only status, count, and differing cell coordinates; malformed-input routes never invoke solution-space or full-solution verification.

Provision and verify both backends independently before a large generation run:

```powershell
python -m src.generator.chess_setup
```

The command exits nonzero unless Stockfish returns a legal UCI probe move and the tablebase returns a verified result for a known three-piece position. Network access is required for first-time Stockfish provisioning and for online tablebase queries; fully offline use requires `CHESS_ENGINE_PATH` and `CHESS_TABLEBASE_PATH`.

Run Chess generation with:

```powershell
python -m src.generator.cli run --domain chess --samples 100 --conversation-type both --job-name chess_100
```

Sudoku tools cover candidate scanning, board and unit validation, naked and hidden singles, locked candidates, move impact, rules, summaries, and solution verification.

Every Sudoku puzzle also includes deterministic `ground_truth`, including the solution, rendered solved board, validity and solvability status, candidates, conflicts, given cells, empty cells, and a suggested move when available. This data is passed into the model prompt and stored in sample metadata.

KenKen uses deterministic 4x4, 5x5, and 6x6 puzzle generation backed by a solution-counting solver. Its canonical `puzzle` value is JSON containing `size` and `cages`; the existing `board` field contains a stable text rendering. Ground truth includes the solved grid, structural violations, solver status, cage evaluations, viable cage tuples, forced values, and a suggested deduction. Its tools also analyze Latin units, cage feasibility, cage/unit intersections, move impact, and the capped solution space.

Kakuro uses deterministic 5x5, 7x7, and 9x9 crossword templates with generated fills and solver-derived uniqueness. Its canonical `puzzle` JSON stores blocked cells plus ordered across/down runs and clue sums. Ground truth includes the solution grid, structural and solvability status, run evaluations, compact distinct-digit combinations, cell candidates, and a suggested crossing-run deduction. Its tools also expose run topology, crossing candidates, feasibility, move impact, and capped solution-space evidence.

Star Battle uses deterministic, connected-region 5x5, 6x6, and 7x7 layouts backed by a solution-counting solver. Its canonical `puzzle` JSON stores `size`, `stars_per_unit`, and complete region membership; its solution string uses `*` and `.` in row-major order. Its tools cover candidates, validation, verification, row/column/region quota capacity, adjacency exclusions, and capped solution-space evidence.

Nonogram uses deterministic solver-verified 5x5, 6x6, 8x8, and 10x10 clue grids. Its canonical `puzzle` JSON stores `height`, `width`, `row_clues`, and `column_clues`; solutions use `#` and `.` in row-major order. Its tools cover row/column pattern enumeration, overlap deductions, cross-line propagation, validation, verification, and capped solution-space evidence.

Hitori uses deterministic solver-verified 4x4 through 7x7 number grids. Its canonical `puzzle` JSON stores `size` and `grid`; solutions use a `#`/`.` shaded-cell mask in row-major order. Its tools cover row/column duplicate groups, forced shading, adjacency risks, unshaded connectivity, validation, verification, and capped solution-space evidence.

Nurikabe uses solver-verified clue islands and sea masks. Its tools cover island capacity and separation, forced deductions, sea connectivity, 2x2-sea risks, validation, verification, and capped solution-space evidence.

Othello stores canonical JSON with a 64-character `B`/`W`/`.` board, `side_to_move`, and `consecutive_passes`, rendered with `a1`–`h8` coordinates. Seeded legal playouts and symmetry variants cover all four difficulties. Easy/medium/hard recommendations use deterministic alpha-beta depths 2/3/4; expert positions have at most ten empties and are searched to completion. Only completed searches are labelled exact. Its 10 tools cover board validation, legal moves and flips, move application, disc counts, mobility, positional features, move comparison, exact endgames, state summary, and rules.

Minesweeper stores canonical visible-state JSON (`width`, `height`, `total_mines`, and `visible`) while the generated mine mask remains persisted ground truth. Boards use 5×5/4, 8×8/10, 9×9/15, and 12×12/25 layouts. The solver decomposes clue-frontier components, enumerates component assignments, and combines them with total-mine dynamic programming and off-frontier combinations to produce exact solution counts and probabilities. Its 12 tools cover validation, neighbor equations, frontier components, forced safe/mine scans, probability, flags, chords, solution space, explicit verification, summary, and rules. Hint and probability prompts never receive the mine mask.

Wordle stores canonical JSON with up to six `{guess, feedback}` entries, hard-mode state, and `G`/`Y`/`B` feedback; the answer remains persisted ground truth. Duplicate letters use the classic two-pass scoring algorithm. Candidate filtering, letter-count constraints, hard-mode validation, frequency scoring, entropy partitions, and deterministic ranking run locally against 2,315 answers and 12,972 accepted guesses. Its 13 tools cover input, feedback, history, candidates, constraints, frequency, entropy, ranking, hard mode, duplicate letters, explicit verification, summary, and rules. Non-verification prompt projections suppress the target. Vocabulary source details, normalized checksums, CC BY-SA attribution, and trademark notice are committed in `src/generator/data/WORDLE_VOCABULARY.md`.

Model prompts use domain projections rather than repeating complete stored ground truth. Full sample metadata remains in JSONL, while large candidate collections are summarized in requests to protect the model context window. KenKen sends its cage layout once in the rendered board, limits tool evidence to the three most constrained cages, and summarizes large candidate sets by count plus examples. `generation.max_prompt_characters` (default `50000`) prevents an oversized request from reaching the API and records a `prompt_budget_exceeded` event with the complete credential-free prompt.

## Pipeline Documentation
For the internal execution model, stage-by-stage processing, sample lifecycle, and extension points, see [PIPELINE.md](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/PIPELINE.md).
