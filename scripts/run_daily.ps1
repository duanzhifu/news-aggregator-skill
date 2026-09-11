$ErrorActionPreference = 'Stop'

# 薄壳：转发到跨平台入口 run_daily.py（保留全部进程契约：cwd/日志/浏览器通道/Vault 校验）。
# 历史计划任务仍指向本文件也能跑；新部署建议直接指向 run_daily.py。
# 计划任务 PATH 很瘦，优先用本机 Python312 绝对路径，再退 $env:PYTHON / py / python。
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python312 = 'C:\Users\86139\AppData\Local\Programs\Python\Python312\python.exe'
if ($env:PYTHON) {
    $launcher = $env:PYTHON
} elseif (Test-Path -LiteralPath $python312) {
    $launcher = $python312
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $launcher = 'py'
} else {
    $launcher = 'python'
}
& $launcher (Join-Path $scriptDir 'run_daily.py')
exit $LASTEXITCODE
