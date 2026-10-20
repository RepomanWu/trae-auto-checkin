# 无 Python 版导出：从本机 Trae 客户端导出可粘贴到面板的 JSON
# Windows 自带 PowerShell，无需安装任何东西。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File export_local.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File export_local.ps1 -AppDirs "Trae CN,TRAE SOLO CN"
#
# 或者干脆不用脚本：用记事本打开
#   %APPDATA%\Trae CN\User\globalStorage\storage.json
# Ctrl+A、Ctrl+C 全选复制，整份粘贴到面板「账号」→「登录态密文」→「导入账号」即可。

param([string]$AppDirs = 'Trae CN,TRAE SOLO CN,Trae,TRAE SOLO')

$ErrorActionPreference = 'SilentlyContinue'
$key = 'iCubeAuthInfo://icube.cloudide'
$dirs = $AppDirs.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$found = 0

foreach ($d in $dirs) {
    $p = Join-Path $env:APPDATA (Join-Path $d 'User\globalStorage\storage.json')
    if (-not (Test-Path $p)) { continue }
    try {
        $s = Get-Content -Path $p -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Host "跳过 $d ：storage.json 解析失败"
        continue
    }

    $prop = $s.PSObject.Properties[$key]
    if (-not $prop -or -not $prop.Value) {
        Write-Host "跳过 $d ：无登录态（该客户端没登录过）"
        continue
    }

    $dcKey = ($s.PSObject.Properties.Name | Where-Object { $_ -like 'iCubeAuthInfo://icube-dc:*' } | Select-Object -First 1)
    $deviceId = ''
    if ($dcKey) { $deviceId = ($dcKey -split ':')[-1] }

    $ver = "$($s.iCubeLastVersion)"
    $obj = [ordered]@{
        app_dir          = $d
        blob             = "$($prop.Value)"
        machine_id       = "$($s.'telemetry.machineId')"
        device_id        = $deviceId
        ide_version      = $ver
        ide_version_code = $ver.Replace('.', '')
        device_type      = 'windows'
        os_version       = 'Windows'
    }
    $found++
    Write-Host ""
    Write-Host "== $d ==（下面这一整行 JSON 粘贴到面板「账号」→「登录态密文」）"
    Write-Host ($obj | ConvertTo-Json -Compress -Depth 4)
}

if ($found -eq 0) {
    Write-Host ""
    Write-Host "未找到任何登录态。请先打开 Trae 客户端登录一次；或用 -AppDirs 指定客户端目录名。"
} else {
    Write-Host ""
    Write-Host "提示：上面的 JSON 含可解出账号令牌的密文，请勿外传（含机器与设备指纹）。"
}
