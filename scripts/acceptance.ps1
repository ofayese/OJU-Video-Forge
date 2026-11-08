param(
    [ValidateSet('acceptance', 'integration')]
    [string]$Environment = 'acceptance'
)

$ErrorActionPreference = 'Stop'
$project = "oju-video-forge-$Environment"
$ids = @(& docker ps -q --filter "label=com.docker.compose.project=$project")
if ($LASTEXITCODE -ne 0 -or -not $ids) { throw 'Start the selected isolated environment first.' }
$containers = @(& docker inspect @ids | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Unable to verify test container labels.' }
foreach ($container in $containers) {
    if ($container.Config.Labels.'com.docker.compose.project' -ne $project) {
        throw 'Unexpected container project identity.'
    }
    foreach ($mount in $container.Mounts) {
        if ($mount.Type -eq 'volume' -and -not $mount.Name.StartsWith("${project}_")) {
            throw 'Test environment contains a shared or unexpected writable volume.'
        }
        if ($mount.Type -eq 'bind' -and $mount.RW) { throw 'Writable host mounts are not allowed in acceptance.' }
    }
    foreach ($network in $container.NetworkSettings.Networks.PSObject.Properties.Name) {
        if (-not $network.StartsWith("${project}_")) { throw 'Unexpected shared test network.' }
    }
}
& "$PSScriptRoot\forge.ps1" -Environment $Environment run --rm --no-deps `
    --entrypoint python api -m scripts.docker_render_smoke
exit $LASTEXITCODE
