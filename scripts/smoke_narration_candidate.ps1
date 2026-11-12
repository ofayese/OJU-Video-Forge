[CmdletBinding()]
param(
    [string]$Image = "ovf-narration:minimal-ffmpeg-verified",
    [string]$ModelVolume = "oju-video-forge_narration-models",
    [string]$FixtureImage = "oju-video-forge-tests:local",
    [ValidateRange(60, 900)]
    [int]$TimeLimitSeconds = 300
)

$ErrorActionPreference = "Stop"
$workPath = Join-Path $env:TEMP ("ovf-narration-smoke-" + [guid]::NewGuid().ToString("N"))
$containerName = "ovf-narration-smoke-" + [guid]::NewGuid().ToString("N")
$stdoutPath = Join-Path $workPath "stdout.json"
$stderrPath = Join-Path $workPath "stderr.log"
$runtimeScript = Join-Path $PSScriptRoot "smoke_narration_runtime.py"
$containerCreated = $false

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

New-Item -ItemType Directory -Path $workPath | Out-Null

try {
    $imageSource = @'
import sys
from pathlib import Path

glyphs = {
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
}
width, height, scale = 640, 360, 10
pixels = bytearray((24, 64, 112)) * (width * height)
x, y = 80, 145
for char in "SECURITY":
    if char == " ":
        x += scale * 4
        continue
    for row, bits in enumerate(glyphs[char]):
        for column, bit in enumerate(bits):
            if bit == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        offset = ((y + row * scale + dy) * width + x + column * scale + dx) * 3
                        pixels[offset:offset + 3] = bytes((255, 255, 255))
    x += scale * 6
Path(sys.argv[1]).write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)
'@

    $imageSource | & docker run --rm -i --network none --read-only --cap-drop ALL `
        --security-opt no-new-privileges:true --user 10001:10001 --cpus 1 `
        --memory 256m --memory-swap 256m --pids-limit 32 `
        --tmpfs /tmp:rw,noexec,nosuid,size=32m `
        --mount "type=bind,source=$workPath,target=/io" `
        --entrypoint python $FixtureImage - /io/frame.ppm
    Assert-True ($LASTEXITCODE -eq 0) "Synthetic image generation failed."

    & docker run --rm --network none --read-only --cap-drop ALL `
        --security-opt no-new-privileges:true --user 10001:10001 --cpus 1 `
        --memory 256m --memory-swap 256m --pids-limit 32 `
        --tmpfs /tmp:rw,noexec,nosuid,size=32m `
        --mount "type=bind,source=$workPath,target=/io" `
        --mount "type=volume,source=$ModelVolume,target=/models/asr,readonly" `
        --entrypoint espeak-ng $Image -w /io/voice.wav `
        "This is a synthetic narration security test."
    Assert-True ($LASTEXITCODE -eq 0) "Synthetic speech generation failed."

    & docker run --rm --network none --read-only --cap-drop ALL `
        --security-opt no-new-privileges:true --user 10001:10001 --cpus 1 `
        --memory 512m --memory-swap 512m --pids-limit 64 `
        --tmpfs /tmp:rw,noexec,nosuid,size=64m `
        --mount "type=bind,source=$workPath,target=/io" `
        --entrypoint ffmpeg $FixtureImage -nostdin -hide_banner -loglevel error `
        -loop 1 -framerate 12 -i /io/frame.ppm -i /io/voice.wav -t 6 `
        -c:v libx264 -pix_fmt yuv420p -c:a aac -ac 1 -b:a 64k -threads 1 `
        -movflags +faststart /io/source.mp4
    Assert-True ($LASTEXITCODE -eq 0) "H.264/AAC source fixture generation failed."

    $sourcePath = Join-Path $workPath "source.mp4"
    Assert-True (Test-Path -LiteralPath $sourcePath) "Synthetic media fixture is missing."
    $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $fixtureImageId = (& docker image inspect --format "{{.Id}}" $FixtureImage).Trim()
    Assert-True ($LASTEXITCODE -eq 0) "Fixture image inspection failed."
    $candidateImageId = (& docker image inspect --format "{{.Id}}" $Image).Trim()
    Assert-True ($LASTEXITCODE -eq 0) "Candidate image inspection failed."

    $createArguments = @(
        "create", "--name", $containerName, "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10001:10001", "--cpus", "2", "--memory", "2g",
        "--memory-swap", "2g", "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
        "--mount", "type=bind,source=$workPath,target=/io,readonly",
        "--mount", "type=bind,source=$runtimeScript,target=/opt/forge/smoke_narration_runtime.py,readonly",
        "--mount", "type=volume,source=$ModelVolume,target=/models/asr,readonly",
        "--entrypoint", "python", $Image, "/opt/forge/smoke_narration_runtime.py",
        "--source", "/io/source.mp4", "--source-sha256", $sourceHash
    )
    & docker @createArguments | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) "Could not create the isolated candidate smoke container."
    $containerCreated = $true

    $inspectText = & docker inspect $containerName
    Assert-True ($LASTEXITCODE -eq 0) "Could not inspect the candidate smoke container."
    $inspection = (($inspectText -join "`n") | ConvertFrom-Json)[0]
    $hostConfig = $inspection.HostConfig
    Assert-True ($hostConfig.NetworkMode -eq "none") "Candidate container is not network-isolated."
    Assert-True ($hostConfig.ReadonlyRootfs) "Candidate root filesystem is not read-only."
    Assert-True ($hostConfig.CapDrop -contains "ALL") "Candidate capabilities were not all dropped."
    Assert-True ($hostConfig.SecurityOpt -contains "no-new-privileges:true") "no-new-privileges is missing."
    Assert-True ($inspection.Config.User -eq "10001:10001") "Candidate is not running as UID/GID 10001."
    Assert-True ($hostConfig.NanoCpus -eq 2000000000) "Candidate CPU limit differs from 2 CPUs."
    Assert-True ($hostConfig.Memory -eq 2147483648) "Candidate memory limit differs from 2 GiB."
    Assert-True ($hostConfig.MemorySwap -eq 2147483648) "Candidate memory+swap limit differs from 2 GiB."
    Assert-True ($hostConfig.PidsLimit -eq 128) "Candidate PID limit differs from 128."
    $tmpfsEntry = $hostConfig.Tmpfs.PSObject.Properties | Where-Object Name -eq "/tmp"
    Assert-True ([bool]$tmpfsEntry) "Candidate tmpfs is missing."
    Assert-True ($tmpfsEntry.Value -match "noexec,nosuid") "Candidate tmpfs is not noexec/nosuid."
    $mediaMount = $inspection.Mounts | Where-Object Destination -eq "/io"
    $scriptMount = $inspection.Mounts | Where-Object Destination -eq "/opt/forge/smoke_narration_runtime.py"
    $modelMount = $inspection.Mounts | Where-Object Destination -eq "/models/asr"
    Assert-True ($mediaMount -and -not $mediaMount.RW) "Synthetic input is not mounted read-only."
    Assert-True ($scriptMount -and -not $scriptMount.RW) "Smoke script is not mounted read-only."
    Assert-True ($modelMount -and -not $modelMount.RW -and $modelMount.Name -eq $ModelVolume) "Model volume is not the expected read-only volume."
    Assert-True (-not ($inspection.Mounts | Where-Object Destination -eq "/var/run/docker.sock")) "Docker socket is mounted."
    $environmentNames = @($inspection.Config.Env | ForEach-Object { ($_ -split "=", 2)[0] })
    Assert-True (-not ($environmentNames | Where-Object { $_ -match "DATABASE|POSTGRES|REDIS|DOCKER_SOCKET" })) "Database/Redis credentials or Docker socket environment was passed."

    $process = Start-Process -FilePath "docker" -ArgumentList @("start", "--attach", $containerName) `
        -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if (-not $process.WaitForExit($TimeLimitSeconds * 1000)) {
        & docker stop --time 2 $containerName | Out-Null
        $process.WaitForExit(10000) | Out-Null
        throw "Candidate narration smoke exceeded $TimeLimitSeconds seconds."
    }
    Assert-True ($process.ExitCode -eq 0) "Candidate smoke container failed: $(Get-Content -LiteralPath $stderrPath -Raw)"

    $finalInspection = ((& docker inspect $containerName | ConvertFrom-Json)[0])
    Assert-True ($finalInspection.State.ExitCode -eq 0) "Candidate smoke container exit code was not zero."
    $runtimeResult = Get-Content -LiteralPath $stdoutPath -Raw | ConvertFrom-Json
    Assert-True ($runtimeResult.asr.status -eq "PASS") "Whisper transcription did not pass."
    Assert-True ($runtimeResult.ocr -eq "PASS") "Tesseract OCR did not pass."
    Assert-True ($runtimeResult.tts -eq "PASS") "eSpeak synthesis did not pass."
    Assert-True ($runtimeResult.source_visual_identity -eq "PASS") "Source video stream was not preserved."

    $probeOutput = & docker run --rm --network none --read-only --cap-drop ALL `
        --security-opt no-new-privileges:true --user 10001:10001 `
        --mount "type=volume,source=$ModelVolume,target=/models/asr,readonly" `
        --entrypoint sh $Image -c "touch /models/asr/.ovf-read-only-probe" 2>&1
    Assert-True ($LASTEXITCODE -ne 0) "Model volume unexpectedly allowed a write."
    Assert-True (($probeOutput -join " ") -match "Read-only file system|Permission denied") "Model write probe failed for an unexpected reason."

    [ordered]@{
        schema_version = 1
        image = @{ name = $Image; id = $candidateImageId }
        fixture_image_id = $fixtureImageId
        synthetic_source_sha256 = $sourceHash
        environment = @{
            network = "none"
            root_filesystem = "read-only"
            capabilities = "all dropped"
            no_new_privileges = $true
            user = "10001:10001"
            cpus = 2
            memory = "2 GiB"
            pids_limit = 128
            tmpfs = "512 MiB, noexec, nosuid"
            model_volume = $ModelVolume
            model_volume_read_only = $true
            database_credentials_passed = $false
            docker_socket_mounted = $false
            execution_limit_seconds = $TimeLimitSeconds
        }
        checks = $runtimeResult
        model_volume_write_probe = "DENIED"
    } | ConvertTo-Json -Depth 12
}
finally {
    if ($containerCreated) {
        & docker rm -f $containerName | Out-Null
    }
    if (Test-Path -LiteralPath $workPath) {
        Remove-Item -LiteralPath $workPath -Recurse -Force
    }
}
