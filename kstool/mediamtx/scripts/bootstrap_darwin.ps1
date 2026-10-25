# Download macOS MediaMTX + ffmpeg into bin/darwin*
param(
    [string]$MediaMtxVersion = 'v1.12.2'
)
$ErrorActionPreference = 'Stop'
$Mtx = Split-Path $PSScriptRoot -Parent
$Bin = Join-Path $Mtx 'bin'
$Tmp = Join-Path $env:TEMP ('mediamtx-darwin-' + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
foreach ($d in @('darwin', 'darwin-arm64', 'darwin-x64')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Bin $d) | Out-Null
}

function Get-Mtx([string]$Arch, [string]$DestDir) {
    $name = "mediamtx_${MediaMtxVersion}_darwin_${Arch}.tar.gz"
    $url = "https://github.com/bluenviron/mediamtx/releases/download/${MediaMtxVersion}/$name"
    $archive = Join-Path $Tmp $name
    Write-Host "GET $url"
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
    tar -xzf $archive -C $Tmp
    $exe = Join-Path $Tmp 'mediamtx'
    Copy-Item -Force $exe (Join-Path $DestDir 'mediamtx')
    Copy-Item -Force $exe (Join-Path (Join-Path $Bin 'darwin') 'mediamtx')
}

Get-Mtx 'amd64' (Join-Path $Bin 'darwin-x64')
# Prefer Apple Silicon for the generic darwin/ folder (copied last).
Get-Mtx 'arm64' (Join-Path $Bin 'darwin-arm64')

$FfBase = 'https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0'
$assets = @(
    @{ Dir = 'darwin-arm64'; Asset = 'ffmpeg-darwin-arm64.gz'; CopyDarwin = $true },
    @{ Dir = 'darwin-x64'; Asset = 'ffmpeg-darwin-x64.gz'; CopyDarwin = $false }
)
foreach ($pair in $assets) {
    $gz = Join-Path $Tmp $pair.Asset
    $url = "$FfBase/$($pair.Asset)"
    Write-Host "GET $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $gz -UseBasicParsing
        $out = Join-Path (Join-Path $Bin $pair.Dir) 'ffmpeg'
        $inStream = [System.IO.File]::OpenRead($gz)
        $gzip = New-Object System.IO.Compression.GzipStream($inStream, [System.IO.Compression.CompressionMode]::Decompress)
        $outStream = [System.IO.File]::Create($out)
        $gzip.CopyTo($outStream)
        $outStream.Close(); $gzip.Close(); $inStream.Close()
        if ($pair.CopyDarwin) {
            Copy-Item -Force $out (Join-Path (Join-Path $Bin 'darwin') 'ffmpeg')
        }
    } catch {
        Write-Warning "skip ffmpeg $($pair.Dir): $_"
    }
}

Remove-Item -Recurse -Force $Tmp
Write-Host 'done'
Get-ChildItem -Recurse $Bin | Where-Object { $_.Name -in @('mediamtx','ffmpeg') -and $_.FullName -match 'darwin' } | Format-Table FullName, Length -AutoSize
