param(
    [string]$ManagerScript = (Join-Path $PSScriptRoot "start_scout_manager.ps1")
)

$ErrorActionPreference = "Stop"
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $ManagerScript,
    [ref]$tokens,
    [ref]$parseErrors
) | Out-Null

if ($parseErrors.Count -gt 0) {
    foreach ($parseError in $parseErrors) {
        [Console]::Error.WriteLine(
            ("{0}:{1}: {2}" -f $parseError.Extent.StartLineNumber,
                $parseError.Extent.StartColumnNumber, $parseError.Message)
        )
    }
    exit 1
}

$source = Get-Content -LiteralPath $ManagerScript -Raw -Encoding UTF8
if ($source -match '(?im)^\s*\$pid\s*=') {
    [Console]::Error.WriteLine(
        "Scout Manager cannot assign to PowerShell's read-only PID variable."
    )
    exit 1
}

Write-Output "Scout Manager PowerShell syntax and reserved variables: OK"
