[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('setup', 'import-auto', 'import-cpu', 'import-intel-gpu', 'import-nvidia-gpu', 'status', 'remove-document', 'cache-clean')]
    [string]$Command,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).ProviderPath
$MainProject = Join-Path $RepositoryRoot 'workspaces\decision-platform\python-services'
$CpuProject = Join-Path $RepositoryRoot 'capstone-rag\ocr\cpu'
$IntelProject = Join-Path $RepositoryRoot 'capstone-rag\ocr\intel'
$NvidiaProject = Join-Path $RepositoryRoot 'capstone-rag\ocr\nvidia'
$Uv = Join-Path $env:LOCALAPPDATA 'CapstoneAITradingCoach\tools\uv-0.11.26\uv.exe'
$OperatorEnvironment = Join-Path $env:LOCALAPPDATA 'CapstoneAITradingCoach\rag\venvs\operator'
$env:UV_PROJECT_ENVIRONMENT = $OperatorEnvironment
$env:CAPSTONE_RAG_LOCAL_ROOT = Join-Path $env:LOCALAPPDATA 'CapstoneAITradingCoach\rag'

if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
    throw 'RAG_UV_PINNED_BINARY_MISSING'
}

# owner identity, ticket, document ID, approved source path는 인증된 local control record만 공급한다.
# wrapper 인자를 전달하면 이 경계가 노출되므로 command line data를 모두 거부한다.
if ($RemainingArguments.Count -ne 0) {
    if ($Command -like 'import-*') {
        Write-Output '{"code":"IMPORT_ARGUMENTS_FORBIDDEN","state":"FAILED"}'
    } else {
        Write-Output '{"code":"CONTENT_COMMAND_INVALID","state":"FAILED"}'
    }
    exit 2
}

$Lane = 'cpu'
if ($Command -eq 'import-auto') {
    $VideoNames = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
    if ($VideoNames -match 'NVIDIA') {
        $Lane = 'nvidia'
    } elseif ($VideoNames -match 'Intel.*Arc') {
        $Lane = 'intel'
    }
} elseif ($Command -eq 'import-intel-gpu') {
    $Lane = 'intel'
} elseif ($Command -eq 'import-nvidia-gpu') {
    $VideoNames = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
    if ($VideoNames -notmatch 'NVIDIA') {
        Write-Output '{"code":"NOT_RUN_NO_NVIDIA","state":"FAILED"}'
        exit 3
    }
    $Lane = 'nvidia'
}

switch ($Lane) {
    'intel' {
        $env:CAPSTONE_RAG_OCR_PROJECT = $IntelProject
        $OpenVinoBinding = 'OPENVINO_DEVICE=GPU'
        $env:OPENVINO_DEVICE = $OpenVinoBinding.Split('=', 2)[1]
    }
    'nvidia' { $env:CAPSTONE_RAG_OCR_PROJECT = $NvidiaProject }
    default { $env:CAPSTONE_RAG_OCR_PROJECT = $CpuProject }
}
$env:CAPSTONE_RAG_OCR_LANE = $Lane

$global:LASTEXITCODE = 127
Push-Location -LiteralPath $env:LOCALAPPDATA
try {
    & $Uv run --project $MainProject --frozen python -m app.rag.content_cli $Command @RemainingArguments
    $ProcessExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $ProcessExitCode
