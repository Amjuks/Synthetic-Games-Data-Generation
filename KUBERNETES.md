# Kubernetes execution

The pipeline runs one Kubernetes `Job` per puzzle domain. A job processes its
sample indexes sequentially and persists progress after every index. Kubernetes
retries use the same generator job name and therefore resume from the persisted
`progress.json` file.

Job names follow this format:

```text
puzzle-<domain>-s<samples>-<run-id>
```

For example:

```text
puzzle-sudoku-s1000-20260815t120000z
```

The Kubernetes Job name and the generator `--job-name` are identical. Never run
two pods concurrently with the same name because output files are append-only
and do not use inter-process locking.

## Prerequisites

- A Kubernetes cluster and a working `kubectl` context.
- A container registry that cluster nodes can pull from.
- A default storage class supporting `ReadWriteMany`, or an edited `k8s/pvc.yaml`
  that names such a storage class.
- Cluster egress to the configured model endpoint. Chess also needs egress for
  first-run Stockfish provisioning and online tablebase queries unless local
  backends are configured.

## Build and push

From the repository root:

```powershell
$image = "YOUR_REGISTRY/synthetic-puzzle-generator:2026-08-15"
docker build -t $image .
docker push $image
```

## Environment and model credentials

The launcher reads the repository-root `.env` by default and creates or updates
the `synthetic-puzzles/model-credentials` Kubernetes Secret from it. The `.env`
is excluded from the container image. At runtime, `envFrom` injects every Secret
entry into the container, and the existing `get_config()` implementation reads
those environment variables exactly as it does during a local run.

For example, the existing OpenAI section works without changes:

```dotenv
MODEL_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

You can use another env file:

```powershell
./k8s/run-all.ps1 -Image $image -EnvFile C:\secure\generator.env
```

For an existing Secret managed by External Secrets, Sealed Secrets, or your cloud
secret manager, use `-SkipEnvSync -SecretName <name>`. Anyone with permission to
read Kubernetes Secrets in this namespace can retrieve these credentials.

## Generator configuration

The launcher creates a run-specific ConfigMap named `puzzle-config-<run-id>` from
`config/defaults.yaml` and `config/prompts.yaml`, then mounts both over the image's
configuration files. This makes generation, model defaults, similarity settings,
domain profiles, and prompts configurable without rebuilding the image.

Override either source file when launching:

```powershell
./k8s/run-all.ps1 `
  -Image $image `
  -DefaultsFile C:\configs\defaults.production.yaml `
  -PromptsFile C:\configs\prompts.production.yaml
```

CLI parameters take precedence where applicable. The launcher exposes `Samples`,
`ConversationType`, `MaxTurns`, `Domains`, and `RunId` directly.

## Smoke test

Run a two-sample Sudoku job first:

```powershell
./k8s/run-all.ps1 `
  -Image $image `
  -Domains sudoku `
  -Samples 2 `
  -ConversationType both `
  -RunId smoke01
```

After model credentials, image pulling, and storage are verified, launch the
full run below.

## Start all domains

The default conversation type is `both`. This starts 11 Jobs and generates 1,000
single-turn plus 1,000 multi-turn accepted samples per domain:

```powershell
./k8s/run-all.ps1 -Image $image -Samples 1000
```

For exactly 1,000 accepted records per domain, select one conversation type:

```powershell
./k8s/run-all.ps1 -Image $image -Samples 1000 -ConversationType single_turn
```

Supply a stable run ID when you want predictable names or need to reapply a run:

```powershell
./k8s/run-all.ps1 `
  -Image $image `
  -Samples 1000 `
  -ConversationType single_turn `
  -RunId run01
```

With `-RunId run01`, Sudoku is stored under:

```text
/app/outputs/puzzle-sudoku-s1000-run01/
```

## Monitor

```powershell
kubectl -n synthetic-puzzles get jobs -l puzzle.openai.com/run-id=run01
kubectl -n synthetic-puzzles get pods -l puzzle.openai.com/run-id=run01
kubectl -n synthetic-puzzles logs -f job/puzzle-sudoku-s1000-run01
```

The PVC contains each job's `progress.json`, JSONL records, CSV exports, dataset
statistics, and request/error logs. If a pod fails and Kubernetes retries it,
the CLI resumes from `next_sample_index` using those files.

Eleven parallel jobs produce eleven concurrent model-request streams. If the
endpoint cannot sustain that load, edit the domain list or launch domains in
smaller groups.
