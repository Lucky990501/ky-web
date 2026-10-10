param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$WorkDir,
    [string]$Python = 'python',
    [string]$AppId,
    [switch]$Run,
    [switch]$UseAccessToken
)

$ErrorActionPreference = 'Stop'
$configPath = (Resolve-Path -LiteralPath $Config).Path
if ([string]::IsNullOrWhiteSpace($WorkDir)) {
    $WorkDir = Join-Path (Split-Path -Parent $configPath) 'run'
}
$flowScript = Join-Path $PSScriptRoot 'wechat_draft.py'
$previousAppId = $env:WECHAT_APP_ID
$previousSecret = $env:WECHAT_APP_SECRET
$previousToken = $env:WECHAT_ACCESS_TOKEN
$previousEncoding = $env:PYTHONIOENCODING
$credentialInput = $null
$flowExitCode = 0

try {
    $env:PYTHONIOENCODING = 'utf-8'
    & $Python -B -X utf8 $flowScript $configPath --check --work-dir $WorkDir
    if ($LASTEXITCODE -ne 0) { throw '离线预检失败，尚未上传。请先修正文章或图片。' }
    if ($Run) {
        if ([string]::IsNullOrWhiteSpace($AppId)) { throw '上传需要 -AppId；离线预检不需要凭据。' }
        $env:WECHAT_APP_ID = $AppId
        $env:WECHAT_APP_SECRET = $null
        $env:WECHAT_ACCESS_TOKEN = $null
        Write-Host "目标 AppID：$AppId"
        Write-Host '即将上传封面和正文图片并创建草稿；相同输入会复用已有记录。'
        if ($UseAccessToken) {
            $credentialInput = Read-Host '在本机输入此 AppID 对应的 access_token（不回显）' -AsSecureString
            $env:WECHAT_ACCESS_TOKEN = [Net.NetworkCredential]::new('', $credentialInput).Password
        } else {
            $credentialInput = Read-Host '在本机输入 AppSecret（不回显）' -AsSecureString
            $env:WECHAT_APP_SECRET = [Net.NetworkCredential]::new('', $credentialInput).Password
        }
        if ([string]::IsNullOrWhiteSpace($env:WECHAT_APP_SECRET) -and [string]::IsNullOrWhiteSpace($env:WECHAT_ACCESS_TOKEN)) {
            throw '未输入凭据，上传未执行。'
        }
        & $Python -B -X utf8 $flowScript $configPath --run --work-dir $WorkDir
        $flowExitCode = $LASTEXITCODE
        if ($flowExitCode -eq 0) {
            Write-Host '草稿字段核对通过。请到对应账号草稿箱查看，并检查手机排版和封面裁切。'
        } else {
            Write-Host '上传或核对未完成。保留运行目录，请检查不含密钥的错误提示。'
        }
    }
} finally {
    $env:WECHAT_APP_ID = $previousAppId
    $env:WECHAT_APP_SECRET = $previousSecret
    $env:WECHAT_ACCESS_TOKEN = $previousToken
    $env:PYTHONIOENCODING = $previousEncoding
    if ($credentialInput) { $credentialInput.Dispose() }
}
exit $flowExitCode
