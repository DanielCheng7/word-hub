# 在桌面建「词枢记词器」快捷方式，指向打包好的 exe
$ErrorActionPreference = 'Stop'
$desk = [Environment]::GetFolderPath('Desktop')
$app  = Join-Path (Split-Path $PSScriptRoot -Parent) 'apps\word-hub'
$exe  = Join-Path $app '词枢记词器.exe'
$icon = Join-Path $app 'icon.ico'

if (-not (Test-Path -LiteralPath $exe)) { throw "找不到 exe：$exe" }

# 原来的网页版快捷方式改名保留
$old = Join-Path $desk '词枢记词器.lnk'
$web = Join-Path $desk '词枢记词器（网页版）.lnk'
if ((Test-Path -LiteralPath $old) -and -not (Test-Path -LiteralPath $web)) {
    Move-Item -LiteralPath $old -Destination $web -Force
}

$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut((Join-Path $desk '词枢记词器.lnk'))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $app
$lnk.IconLocation = $icon
$lnk.Description = '词枢 · 英语记词器（桌面版 exe）'
$lnk.Save()

$rows = @()
foreach ($f in (Get-ChildItem $desk -Filter '*.lnk' | Where-Object { $_.Name -like '*词枢*' })) {
    $rows += ('{0}  ->  {1}' -f $f.Name, $sh.CreateShortcut($f.FullName).TargetPath)
}
$rows | Out-File -FilePath (Join-Path $PSScriptRoot 'lnk_check.txt') -Encoding UTF8
