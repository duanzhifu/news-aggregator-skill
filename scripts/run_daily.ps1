$ErrorActionPreference = 'Stop'

$skillRoot = 'C:\Users\86139\.claude\skills\news-aggregator-skill'
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
    $args = @(
        '-u', $pushScript,
        '--source', 'juejin,devto,github,openai,bilibili,youtube_tech',
        '--limit', '15', '--evidence-mode', 'snapshot', '--vault', $vaultPath
    )
    $exitCode = Invoke-PythonUtf8 $args
    Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Export finished with exit code $exitCode."
}
catch {
    $errorDetails = $_ | Out-String
    Write-RunLog "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Export failed: $errorDetails"
    $exitCode = 1
}

exit $exitCode
