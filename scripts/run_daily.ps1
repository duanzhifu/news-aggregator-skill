$ErrorActionPreference = 'Stop'

$skillRoot = 'D:\Obsidian\自动信息获取\_skill\news-aggregator-skill'
$pythonPath = 'C:\Users\86139\AppData\Local\Programs\Python\Python312\python.exe'
$pushScript = Join-Path $skillRoot 'scripts\push_to_obsidian.py'
$vaultFolder = [string]::Concat([char[]](0x81EA, 0x52A8, 0x4FE1, 0x606F, 0x83B7, 0x53D6))
$vaultPath = 'D:\Obsidian\' + $vaultFolder
$browserProfilePath = 'D:\news-aggregator-browser-profile'
$logPath = Join-Path $skillRoot 'logs\daily_task.log'
$logDirectory = Split-Path -Parent $logPath

function Write-RunLog([string]$message) {
    Add-Content -LiteralPath $logPath -Value $message -Encoding UTF8
}

function Invoke-PythonUtf8([string[]]$arguments) {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pythonPath
    $startInfo.WorkingDirectory = $skillRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $startInfo.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $quotedArguments = foreach ($argument in $arguments) {
        '"' + ($argument -replace '"', '\"') + '"'
    }
    $startInfo.Arguments = $quotedArguments -join ' '

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "Unable to start Python process: $pythonPath"
    }

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    if ($stdout) { Write-RunLog $stdout.TrimEnd() }
    if ($stderr) { Write-RunLog $stderr.TrimEnd() }
    return $process.ExitCode
}

$exitCode = 1
try {
    foreach ($path in @($skillRoot, $pythonPath, $pushScript, $vaultPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required path does not exist: $path"
        }
    }

    Set-Location -LiteralPath $skillRoot
    if (-not (Test-Path -LiteralPath $logDirectory)) {
        New-Item -ItemType Directory -Path $logDirectory | Out-Null
    }
    Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Starting daily Obsidian news export."
    $env:NEWS_AGGREGATOR_BROWSER_CHANNEL = 'msedge'
    if (Test-Path -LiteralPath $browserProfilePath -PathType Container) {
        $env:NEWS_AGGREGATOR_BROWSER_PROFILE = $browserProfilePath
        Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Using persistent social browser profile: $browserProfilePath"
    }
    else {
        Remove-Item Env:NEWS_AGGREGATOR_BROWSER_PROFILE -ErrorAction SilentlyContinue
        Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Social browser profile not found; using temporary browser sessions."
    }
    $userInterestsPath = Join-Path $skillRoot 'user_interests.json'
    $topicsArg = @()
    $sourceArg = @()
    $dynamicLimitArg = @()
    if (Test-Path -LiteralPath $userInterestsPath) {
        try {
            $jsonContent = Get-Content -LiteralPath $userInterestsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($jsonContent.topics) {
                $topicsArg = @('--topics', ($jsonContent.topics -join ','))
                Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Loaded topics from user_interests.json: $($jsonContent.topics -join ', ')"
            }
            if ($jsonContent.daily_sources) {
                $sourceArg = @('--source', ($jsonContent.daily_sources -join ','))
                Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Loaded daily_sources from user_interests.json: $($jsonContent.daily_sources -join ', ')"
            }
            if ($null -ne $jsonContent.limit_per_topic) {
                $dynamicLimitArg = @('--dynamic-limit', [string]$jsonContent.limit_per_topic)
                Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Loaded limit_per_topic from user_interests.json: $($jsonContent.limit_per_topic)"
            }
        }
        catch {
            Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Failed to parse user_interests.json: $_"
        }
    }

    $args = @(
        '-u', $pushScript,
        '--limit', '15', '--evidence-mode', 'snapshot', '--vault', $vaultPath
    ) + $sourceArg + $topicsArg + $dynamicLimitArg
    $exitCode = Invoke-PythonUtf8 $args
    Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Export finished with exit code $exitCode."
}
catch {
    $errorDetails = $_ | Out-String
    Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Export failed: $errorDetails"
    $exitCode = 1
}

exit $exitCode
