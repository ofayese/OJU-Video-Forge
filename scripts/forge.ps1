param(
    [ValidateSet('application', 'acceptance', 'integration')]
    [string]$Environment = 'application',
    [switch]$Narration,
    [switch]$Plan,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$projects = @{
    application = 'oju-video-forge'
    acceptance = 'oju-video-forge-acceptance'
    integration = 'oju-video-forge-integration'
}
$project = $projects[$Environment]
$files = @((Join-Path $root 'compose.yaml'))
$envFile = Join-Path $root '.env'
if ($Environment -ne 'application') {
    $files += Join-Path $root "compose.$Environment.yaml"
    $envFile = Join-Path $root ".env.$Environment.local"
}
if ($Narration) {
    if ($Environment -ne 'application') {
        throw 'The narration overlay is supported only for the application environment.'
    }
    $files += Join-Path $root 'compose.narration.yaml'
}

foreach ($key in @('COMPOSE_PROJECT_NAME', 'COMPOSE_FILE', 'COMPOSE_PROFILES')) {
    if ([Environment]::GetEnvironmentVariable($key)) {
        throw "Unset $key before using the environment-specific helper."
    }
}
foreach ($arg in $ComposeArgs) {
    if ($arg -match '^(-[pfv].*|--(project-name|project-directory|env-file|file|volume|volumes|remove-orphans|name)(=|$))') {
        throw 'Project, configuration, storage, and orphan overrides are not allowed.'
    }
}
if ($ComposeArgs -and $ComposeArgs[0] -notin @('build', 'up', 'start', 'stop', 'restart', 'ps', 'logs', 'exec', 'run', 'config', 'pull', 'images')) {
    throw 'Unsupported operation. Use stop for safe shutdown; deletion requires separate owner approval.'
}
if ($Plan) {
    [pscustomobject]@{ Project = $project; Files = $files; EnvironmentFile = $envFile }
    return
}
if (-not $ComposeArgs) { throw 'Supply a Compose operation, such as ps or stop.' }
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Create the private environment file for $Environment before executing Compose."
}

function Get-DatabaseSecret([string]$Path) {
    $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match '^DB_PASSWORD=' }
    if (@($line).Count -ne 1) { throw 'Exactly one DB_PASSWORD assignment is required.' }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value -notmatch '^[A-Za-z0-9_-]{32,}$' -or $value -match 'REPLACE') {
        throw 'Use a unique URL-safe random database secret of at least 32 characters.'
    }
    return $value
}
$secret = Get-DatabaseSecret $envFile
if ($env:DB_PASSWORD -and $env:DB_PASSWORD -ne $secret) {
    throw 'DB_PASSWORD in the shell conflicts with the selected private environment file.'
}
foreach ($other in @('.env', '.env.acceptance.local', '.env.integration.local')) {
    $otherPath = Join-Path $root $other
    if ($otherPath -ne $envFile -and (Test-Path -LiteralPath $otherPath -PathType Leaf)) {
        if ((Get-DatabaseSecret $otherPath) -eq $secret) {
            throw 'Application and test environments must use distinct database secrets.'
        }
    }
}
$arguments = @('compose', '--project-name', $project, '--env-file', $envFile)
foreach ($file in $files) { $arguments += @('--file', $file) }
& docker @arguments @ComposeArgs
exit $LASTEXITCODE
