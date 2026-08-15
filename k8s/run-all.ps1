[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Image,

    [ValidateRange(1, 1000000)]
    [int] $Samples = 1000,

    [ValidateSet("single_turn", "multi_turn", "both")]
    [string] $ConversationType = "both",

    [ValidateRange(1, 100)]
    [int] $MaxTurns = 6,

    [ValidateSet(
        "chess", "sudoku", "kenken", "kakuro", "starbattle", "nonogram",
        "hitori", "nurikabe", "othello", "minesweeper", "wordle"
    )]
    [string[]] $Domains = @(
        "chess", "sudoku", "kenken", "kakuro", "starbattle", "nonogram",
        "hitori", "nurikabe", "othello", "minesweeper", "wordle"
    ),

    [string] $RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'").ToLowerInvariant(),

    [string] $EnvFile = "",

    [string] $DefaultsFile = "",

    [string] $PromptsFile = "",

    [string] $SecretName = "model-credentials",

    [switch] $SkipEnvSync,

    [switch] $SkipInfrastructure
)

$ErrorActionPreference = "Stop"
$namespace = "synthetic-puzzles"
$scriptDir = $PSScriptRoot
$repoRoot = Split-Path $scriptDir -Parent
$templatePath = Join-Path $scriptDir "job-template.yaml"

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $repoRoot ".env"
}
if ([string]::IsNullOrWhiteSpace($DefaultsFile)) {
    $DefaultsFile = Join-Path $repoRoot "config\defaults.yaml"
}
if ([string]::IsNullOrWhiteSpace($PromptsFile)) {
    $PromptsFile = Join-Path $repoRoot "config\prompts.yaml"
}

if ($RunId -cnotmatch '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$') {
    throw "RunId must contain only lowercase letters, digits, and internal hyphens."
}

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw "kubectl is not installed or is not available on PATH."
}

kubectl cluster-info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "kubectl cannot connect to the current cluster/context."
}

if (-not $SkipInfrastructure) {
    kubectl apply -f (Join-Path $scriptDir "namespace.yaml")
    if ($LASTEXITCODE -ne 0) { throw "Failed to apply the namespace." }

    kubectl apply -f (Join-Path $scriptDir "pvc.yaml")
    if ($LASTEXITCODE -ne 0) { throw "Failed to apply the output PVC." }
}

if (-not $SkipEnvSync) {
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        throw "Environment file not found: $EnvFile"
    }
    $resolvedEnvFile = (Resolve-Path -LiteralPath $EnvFile).Path
    $secretManifest = kubectl --namespace $namespace create secret generic $SecretName `
        "--from-env-file=$resolvedEnvFile" `
        --dry-run=client `
        -o yaml
    if ($LASTEXITCODE -ne 0) { throw "Failed to render secret '$SecretName' from $resolvedEnvFile." }
    $secretManifest | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "Failed to apply secret '$SecretName'." }
}
else {
    kubectl --namespace $namespace get secret $SecretName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Secret '$namespace/$SecretName' is missing."
    }
}

foreach ($configFile in @($DefaultsFile, $PromptsFile)) {
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
        throw "Generator configuration file not found: $configFile"
    }
}
$resolvedDefaultsFile = (Resolve-Path -LiteralPath $DefaultsFile).Path
$resolvedPromptsFile = (Resolve-Path -LiteralPath $PromptsFile).Path
$configMapName = "puzzle-config-$RunId"
$configManifest = kubectl --namespace $namespace create configmap $configMapName `
    "--from-file=defaults.yaml=$resolvedDefaultsFile" `
    "--from-file=prompts.yaml=$resolvedPromptsFile" `
    --dry-run=client `
    -o yaml
if ($LASTEXITCODE -ne 0) { throw "Failed to render ConfigMap '$configMapName'." }
$configManifest | kubectl apply -f -
if ($LASTEXITCODE -ne 0) { throw "Failed to apply ConfigMap '$configMapName'." }

$template = Get-Content -LiteralPath $templatePath -Raw
foreach ($domain in $Domains) {
    $jobName = "puzzle-$domain-s$Samples-$RunId"
    if ($jobName.Length -gt 63) {
        throw "Generated Kubernetes job name exceeds 63 characters: $jobName"
    }

    $manifest = $template.Replace("__JOB_NAME__", $jobName).
        Replace("__DOMAIN__", $domain).
        Replace("__RUN_ID__", $RunId).
        Replace("__IMAGE__", $Image).
        Replace("__SECRET_NAME__", $SecretName).
        Replace("__CONFIG_MAP_NAME__", $configMapName).
        Replace("__SAMPLES__", $Samples.ToString()).
        Replace("__CONVERSATION_TYPE__", $ConversationType).
        Replace("__MAX_TURNS__", $MaxTurns.ToString())

    $manifest | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply job $jobName."
    }
}

Write-Host "Started $($Domains.Count) jobs in namespace '$namespace' with run ID '$RunId'."
Write-Host "Monitor: kubectl -n $namespace get jobs -l puzzle.openai.com/run-id=$RunId -w"
