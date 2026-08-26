$ErrorActionPreference = "Stop"

$allowed = @("up", "down", "status", "logs", "smoke", "doctor", "mock", "help", "-h", "--help")
if ($args.Count -lt 1 -or -not $allowed.Contains([string]$args[0])) {
    [Console]::Error.WriteLine("Usage: .\capstone.ps1 <up|down|status|logs|smoke|doctor|mock> [options]")
    exit 1
}

$wsl = Get-Command "wsl.exe" -ErrorAction SilentlyContinue
if ($null -eq $wsl) {
    [Console]::Error.WriteLine("CAPSTONE_ERROR=WSL_REQUIRED")
    exit 1
}
$repoWindows = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoWsl = (& wsl.exe wslpath -a -u $repoWindows).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repoWsl)) {
    [Console]::Error.WriteLine("CAPSTONE_ERROR=WSL_PATH_CONVERSION")
    exit 1
}

& wsl.exe --cd $repoWsl ./capstone @args
exit $LASTEXITCODE
