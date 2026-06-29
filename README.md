# Synthetic Sudoku Conversation Data Generator

## Project Overview
This project generates synthetic Sudoku chat datasets with persistent job state, structured scenario generation, puzzle selection, validation, and diversity checks.

Primary use case:
- Build large Sudoku conversation datasets for training, evaluation, or experimentation.

Key features:
- Generates `single_turn`, `multi_turn`, or both conversation types in one run.
- Uses structured scenarios instead of relying only on LLM creativity.
- Selects puzzles from a persistent puzzle bank and creates transformed or edge-case variants.
- Validates generated outputs before accepting them.
- Rejects overly similar samples and stores rejected attempts for inspection.
- Writes outputs incrementally and can resume interrupted jobs.
- Supports `openai`, `custom_chat`, and mock generation modes.

## Setup Instructions
### Prerequisites
- Python `>=3.10`
- A virtual environment is recommended
- One of the following model backends:
  - OpenAI API
  - A compatible custom chat-completions endpoint
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
| `MODEL_PROVIDER` | Selects the model backend. | string | `openai` | `openai`, `custom_chat` | Optional |

#### OpenAI settings

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key. | string | none | any valid key | Required for `openai` |
| `OPENAI_MODEL` | OpenAI model name. | string | `gpt-4o-mini` | any model accepted by the SDK | Optional |
| `OPENAI_BASE_URL` | Override base URL for the OpenAI client. | string | none | valid URL | Optional |
| `OPENAI_TEMPERATURE` | Sampling temperature. | float | `0.8` | provider-dependent | Optional |
| `OPENAI_MAX_TOKENS` | Maximum output tokens. | int | `800` | positive integers | Optional |

#### Custom chat-completions settings

| Name | Description | Type | Default | Allowed values | Required |
|---|---|---:|---|---|---|
| `CUSTOM_API_URL` | Chat-completions endpoint URL. | string | none | valid URL | Required for `custom_chat` |
| `CUSTOM_API_KEY` | Bearer token for the custom endpoint. | string | none | any valid token | Optional unless endpoint requires auth |
| `CUSTOM_MODEL` | Model name sent to the custom endpoint. | string | `gpt-oss-120b` | any endpoint-supported model | Optional |
| `CUSTOM_TEMPERATURE` | Sampling temperature. | float | `0.1` | endpoint-dependent | Optional |
| `CUSTOM_MAX_TOKENS` | Maximum tokens sent as `max_tokens`. | int | `800` | positive integers | Optional |
| `CUSTOM_REASONING` | Included in the system message as `Reasoning: ...`. | string | `Low` | any string | Optional |
| `CUSTOM_ENABLE_THINKING` | Sent under `chat_template_kwargs.enable_thinking`. | bool | `false` | `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off` | Optional |

### Configuration Files
- [config/defaults.yaml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/config/defaults.yaml): runtime defaults for generation, similarity, puzzles, and storage.
- [config/prompts.yaml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/config/prompts.yaml): system and task prompts used to build generation requests.

No additional external datasets are required by the current code. The current implementation uses a built-in puzzle bank and writes a persistent copy to the output directory.

## Usage
### Run the CLI

```bash
python -m src.generator.cli --help
python -m src.generator.cli run --samples 5
python -m src.generator.cli status --job-name my_job
```

The project also exposes a console script from [pyproject.toml](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/pyproject.toml):

```bash
sudoku-generator run --samples 5
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
| `--samples` | Total sample indexes to process. If resuming a job with a larger existing total, the existing total is preserved. | int | `config.defaults.samples` -> `10` | positive integers | Optional |
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

### Example Commands
#### Basic single run

```bash
python -m src.generator.cli run --samples 5
```

Uses the defaults from `config/defaults.yaml` and generates both conversation types unless overridden.

#### Single-turn only

```bash
python -m src.generator.cli run --samples 100 --conversation-type single_turn --job-name sudoku_single_100
```

Creates only single-turn samples and stores them under `outputs/sudoku_single_100/`.

#### Multi-turn focused run

```bash
python -m src.generator.cli run --samples 50 --conversation-type multi_turn --max-turns 8 --job-name multi_coaching
```

Useful when you want only multi-turn data and want scenarios to be allowed up to eight exchanges.

#### Resume an interrupted job

```bash
python -m src.generator.cli run --samples 500 --conversation-type both --job-name my_large_job
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

### File Details
#### `progress.json`
Contains:
- Job identifiers
- `status`
- `completed`
- `total`
- `next_sample_index`
- `accepted_samples`
- `rejected_samples`
- `distribution_stats`
- output file paths
- `last_error` and traceback when failures occur

#### `samples.jsonl`
Each line is one accepted sample with fields such as:
- `sample_id`
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
- `conversation`
- `output`

#### `rejected_samples.jsonl`
Each line stores a rejected generation attempt with:
- `sample_index`
- `attempt`
- `rejection_type`
- `reasons`
- `metrics`
- `scenario`
- `puzzle_metadata`
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
- `puzzle_reuse_distribution`
- `conversation_type_distribution`
- `conversation_length_distribution`

#### `single_turn.csv`
Flattened fields include:
- identifiers and metadata columns
- `prompt`
- `response`
- `board`

#### `multi_turn.csv`
Flattened fields include:
- identifiers and metadata columns
- `messages` as serialized JSON
- `board`

## Pipeline Documentation
For the internal execution model, stage-by-stage processing, sample lifecycle, and extension points, see [PIPELINE.md](C:/Users/emertxe-87/Desktop/Synthetic%20Sudoku%20Dataset/PIPELINE.md).
