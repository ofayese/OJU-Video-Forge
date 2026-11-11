[CmdletBinding()]
param(
    [string]$Image = 'ovf-narration:minimal-ffmpeg-verified',
    [string]$ModelVolume = 'oju-video-forge_narration-models',
    [ValidateRange(60, 600)]
    [int]$TimeLimitSeconds = 300
)

$ErrorActionPreference = 'Stop'
$containerName = "ovf-narration-smoke-$([guid]::NewGuid().ToString('N'))"
$generatorName = "$containerName-fixture"
$fixtureDirectory = Join-Path $env:TEMP $generatorName
$fixtureVideo = Join-Path $fixtureDirectory 'video.mp4'
$harness = Join-Path $PSScriptRoot 'smoke_narration_runtime.py'
$start = [datetime]::UtcNow
$process = $null
$generatorProcess = $null
$inspect = $null

try {
    New-Item -ItemType Directory -Path $fixtureDirectory | Out-Null
    $generatorInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $generatorInfo.FileName = 'docker'
    $generatorInfo.UseShellExecute = $false
    $generatorInfo.RedirectStandardOutput = $true
    $generatorInfo.RedirectStandardError = $true
    $fixtureArguments = @(
        'run', '--name', $generatorName,
        '--network', 'none',
        '--read-only',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--cpus', '1',
        '--memory', '512m',
        '--memory-swap', '512m',
        '--pids-limit', '32',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
        '--mount', "type=bind,source=$fixtureDirectory,target=/output",
        '--entrypoint', 'ffmpeg',
        'oju-video-forge-tests:local',
        '-nostdin', '-hide_banner', '-v', 'error',
        '-f', 'lavfi', '-i', 'color=c=black:s=640x360:r=12:d=6',
        '-vf', "drawtext=fontcolor=white:fontsize=36:x=32:y=32:text='SYNTHETIC SECURITY'",
        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '/output/video.mp4'
    )
    foreach ($argument in $fixtureArguments) {
        [void]$generatorInfo.ArgumentList.Add($argument)
    }
    $generatorProcess = [System.Diagnostics.Process]::new()
    $generatorProcess.StartInfo = $generatorInfo
    if (-not $generatorProcess.Start()) {
        throw 'fixture_generator_failed_to_start'
    }
    $generatorStdoutTask = $generatorProcess.StandardOutput.ReadToEndAsync()
    $generatorStderrTask = $generatorProcess.StandardError.ReadToEndAsync()
    if (-not $generatorProcess.WaitForExit(30000)) {
        & docker stop --time 2 $generatorName 2>$null | Out-Null
        if (-not $generatorProcess.WaitForExit(5000)) {
            & docker kill $generatorName 2>$null | Out-Null
            if (-not $generatorProcess.WaitForExit(5000)) {
                Stop-Process -Id $generatorProcess.Id -Force
            }
        }
        throw 'fixture_generator_timeout_after_30_seconds'
    }
    $generatorProcess.WaitForExit()
    if ($generatorProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $fixtureVideo)) {
        $generatorStderr = $generatorStderrTask.GetAwaiter().GetResult().Trim()
        throw "fixture_generator_failed`n$generatorStderr"
    }
    $generatorInspectText = & docker inspect $generatorName 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $generatorInspectText) {
        throw 'unable_to_verify_fixture_generator_configuration'
    }
    $generatorInspect = @($generatorInspectText | ConvertFrom-Json)[0]
    $generatorHost = $generatorInspect.HostConfig
    $generatorSecurityOpt = @($generatorHost.SecurityOpt)
    $generatorCapDrop = @($generatorHost.CapDrop)
    $generatorOutputMount = @($generatorInspect.Mounts | Where-Object {
        $_.Destination -eq '/output' -and [bool]$_.RW
    })
    $generatorVerified = [ordered]@{
        network_none = $generatorHost.NetworkMode -eq 'none'
        root_read_only = [bool]$generatorHost.ReadonlyRootfs
        all_capabilities_dropped = $generatorCapDrop -contains 'ALL'
        no_new_privileges = $generatorSecurityOpt -contains 'no-new-privileges:true'
        cpu_limit_one = [long]$generatorHost.NanoCpus -eq 1000000000
        memory_limit_512_mib = [long]$generatorHost.Memory -eq 536870912
        pids_limit_32 = [long]$generatorHost.PidsLimit -eq 32
        output_scope_is_single_fixture_directory = $generatorOutputMount.Count -eq 1
        no_docker_socket = @($generatorInspect.Mounts | Where-Object {
            $_.Destination -eq '/var/run/docker.sock'
        }).Count -eq 0
    }
    if (@($generatorVerified.Values | Where-Object { -not $_ }).Count -gt 0) {
        throw "fixture_generator_isolation_verification_failed`n$(ConvertTo-Json $generatorVerified -Compress)"
    }
    $fixtureHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $fixtureVideo).Hash.ToLowerInvariant()

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = 'docker'
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $arguments = @(
        'run', '--name', $containerName,
        '--network', 'none',
        '--read-only',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--cpus', '2',
        '--memory', '2g',
        '--memory-swap', '2g',
        '--pids-limit', '128',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=512m',
        '--mount', "type=volume,source=$ModelVolume,target=/models/asr,readonly",
        '--mount', "type=bind,source=$fixtureVideo,target=/input/source.mp4,readonly",
        '--mount', "type=bind,source=$harness,target=/opt/forge/smoke_narration_runtime.py,readonly",
        '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--env', 'HF_HUB_OFFLINE=1',
        '--env', 'HF_HUB_DISABLE_TELEMETRY=1',
        '--env', 'NARRATION_ASR_MODEL=/models/asr/faster-whisper-tiny',
        '--env', 'OVF_SMOKE_VIDEO=/input/source.mp4',
        '--env', "OVF_SMOKE_VIDEO_SHA256=$fixtureHash",
        '--env', "OVF_SMOKE_TIME_LIMIT_SECONDS=$TimeLimitSeconds",
        '--entrypoint', 'python',
        $Image, '/opt/forge/smoke_narration_runtime.py'
    )
    foreach ($argument in $arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw 'docker_run_failed_to_start'
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(($TimeLimitSeconds + 30) * 1000)) {
        & docker stop --time 5 $containerName 2>$null | Out-Null
        if (-not $process.WaitForExit(10000)) {
            & docker kill $containerName 2>$null | Out-Null
            if (-not $process.WaitForExit(5000)) {
                Stop-Process -Id $process.Id -Force
            }
        }
        throw "smoke_host_timeout_after_$($TimeLimitSeconds + 30)_seconds"
    }
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult().Trim()
    $stderr = $stderrTask.GetAwaiter().GetResult().Trim()
    $runExitCode = $process.ExitCode

    $inspectText = & docker inspect $containerName 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $inspectText) {
        throw 'unable_to_verify_smoke_container_configuration'
    }
    $inspect = @($inspectText | ConvertFrom-Json)[0]
    $hostConfig = $inspect.HostConfig
    $mounts = @($inspect.Mounts)
    $modelMount = @($mounts | Where-Object {
        $_.Type -eq 'volume' -and $_.Name -eq $ModelVolume -and $_.Destination -eq '/models/asr'
    })
    $fixtureMount = @($mounts | Where-Object {
        $_.Destination -eq '/input/source.mp4' -and -not [bool]$_.RW
    })
    $forbiddenEnvironment = @(
        $inspect.Config.Env |
            ForEach-Object { ($_ -split '=', 2)[0] } |
            Where-Object {
                $_ -match '(?i)DATABASE|POSTGRES|REDIS|SQLALCHEMY|^DB_|^PGPASSWORD|^PGUSER'
            }
    )
    $dockerSocketMount = @($mounts | Where-Object {
        $_.Source -eq '/var/run/docker.sock' -or $_.Destination -eq '/var/run/docker.sock'
    })
    $tmpfs = $hostConfig.Tmpfs.PSObject.Properties['/tmp'].Value
    $securityOpt = @($hostConfig.SecurityOpt)
    $capDrop = @($hostConfig.CapDrop)

    $verified = [ordered]@{
        network_none = $hostConfig.NetworkMode -eq 'none'
        root_read_only = [bool]$hostConfig.ReadonlyRootfs
        all_capabilities_dropped = $capDrop -contains 'ALL'
        no_new_privileges = $securityOpt -contains 'no-new-privileges:true'
        cpu_limit_two = [long]$hostConfig.NanoCpus -eq 2000000000
        memory_limit_two_gib = [long]$hostConfig.Memory -eq 2147483648
        swap_limit_equals_memory = [long]$hostConfig.MemorySwap -eq 2147483648
        pids_limit_128 = [long]$hostConfig.PidsLimit -eq 128
        restricted_tmpfs = [string]$tmpfs -match 'noexec' -and [string]$tmpfs -match 'nosuid'
        model_volume_read_only = $modelMount.Count -eq 1 -and -not [bool]$modelMount[0].RW
        synthetic_input_read_only = $fixtureMount.Count -eq 1
        no_application_credentials = $forbiddenEnvironment.Count -eq 0
        no_docker_socket = $dockerSocketMount.Count -eq 0
        non_root_user = $inspect.Config.User -eq '10001:10001'
        execution_time_within_limit = ([datetime]::UtcNow - $start).TotalSeconds -le ($TimeLimitSeconds + 30)
    }
    if ($runExitCode -ne 0) {
        throw "smoke_container_exit_$runExitCode`n$stdout`n$stderr"
    }
    $smoke = $stdout | ConvertFrom-Json
    if ($smoke.status -ne 'PASS' -or @($verified.Values | Where-Object { -not $_ }).Count -gt 0) {
        throw "smoke_or_isolation_verification_failed`n$stdout`n$(ConvertTo-Json $verified -Compress)"
    }

    $result = [ordered]@{
        status = 'PASS'
        image = $Image
        image_digest = $inspect.Image
        container_id = $inspect.Id
        synthetic_fixture_sha256 = $fixtureHash
        fixture_generator = @{
            image = 'oju-video-forge-tests:local'
            image_digest = $generatorInspect.Image
            network = 'none'
            isolation = $generatorVerified
            execution_limit_seconds = 30
        }
        elapsed_seconds = [math]::Round(([datetime]::UtcNow - $start).TotalSeconds, 3)
        time_limit_seconds = $TimeLimitSeconds
        isolation = $verified
        smoke = $smoke
    }
    ConvertTo-Json -InputObject $result -Depth 20 -Compress
}
finally {
    & docker rm -f $containerName 2>$null | Out-Null
    & docker rm -f $generatorName 2>$null | Out-Null
    if ($generatorProcess) {
        $generatorProcess.Dispose()
    }
    if ($fixtureVideo -and (Test-Path -LiteralPath $fixtureVideo)) {
        Remove-Item -LiteralPath $fixtureVideo -Force
    }
    if ($fixtureDirectory -and (Test-Path -LiteralPath $fixtureDirectory)) {
        Remove-Item -LiteralPath $fixtureDirectory
    }
    if ($process) {
        $process.Dispose()
    }
}
