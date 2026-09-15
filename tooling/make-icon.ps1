# Generates tooling/sleepy.ico — a simple crescent-moon + stars icon (multi-resolution PNG-in-ICO).
# Run once (or whenever the icon should be regenerated): powershell -File tooling\make-icon.ps1
Add-Type -AssemblyName System.Drawing

function New-MoonPng([int]$size) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)

    # Dark rounded-square backdrop (matches the app's dark-mode aesthetic)
    $bgColor = [System.Drawing.Color]::FromArgb(255, 30, 33, 43)
    $bgBrush = New-Object System.Drawing.SolidBrush $bgColor
    $pad = [int]($size * 0.04)
    $rectSize = $size - (2 * $pad)
    $radius = [int]($size * 0.22)
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $radius * 2
    $path.AddArc($pad, $pad, $d, $d, 180, 90)
    $path.AddArc($pad + $rectSize - $d, $pad, $d, $d, 270, 90)
    $path.AddArc($pad + $rectSize - $d, $pad + $rectSize - $d, $d, $d, 0, 90)
    $path.AddArc($pad, $pad + $rectSize - $d, $d, $d, 90, 90)
    $path.CloseFigure()
    $g.FillPath($bgBrush, $path)

    # Moon body (soft yellow)
    $moonColor = [System.Drawing.Color]::FromArgb(255, 247, 209, 108)
    $moonBrush = New-Object System.Drawing.SolidBrush $moonColor
    $moonD = [int]($size * 0.58)
    $moonX = [int]($size * 0.24)
    $moonY = [int]($size * 0.21)
    $g.FillEllipse($moonBrush, $moonX, $moonY, $moonD, $moonD)

    # Bite out a crescent using the backdrop color, offset up-right
    $biteD = [int]($moonD * 0.92)
    $biteX = $moonX + [int]($moonD * 0.34)
    $biteY = $moonY - [int]($moonD * 0.12)
    $g.FillEllipse($bgBrush, $biteX, $biteY, $biteD, $biteD)

    # A couple of small stars
    $starBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 247, 209, 108))
    $s1 = [int]($size * 0.07)
    $g.FillEllipse($starBrush, [int]($size*0.72), [int]($size*0.20), $s1, $s1)
    $s2 = [int]($size * 0.045)
    $g.FillEllipse($starBrush, [int]($size*0.80), [int]($size*0.34), $s2, $s2)

    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    [byte[]]$bytes = $ms.ToArray()
    $g.Dispose(); $bmp.Dispose(); $ms.Dispose()
    , $bytes
}

$sizes = @(16, 32, 48, 256)
$pngs = @{}
foreach ($s in $sizes) { $pngs[$s] = New-MoonPng $s }

$outPath = Join-Path $PSScriptRoot "sleepy.ico"
$fs = [System.IO.File]::Open($outPath, [System.IO.FileMode]::Create)
$bw = New-Object System.IO.BinaryWriter $fs

# ICONDIR
$bw.Write([UInt16]0)      # reserved
$bw.Write([UInt16]1)      # type = icon
$bw.Write([UInt16]$sizes.Count)

$headerSize = 6 + (16 * $sizes.Count)
$offset = $headerSize
foreach ($s in $sizes) {
    $data = $pngs[$s]
    $wByte = if ($s -ge 256) { 0 } else { $s }
    $bw.Write([byte]$wByte)      # width
    $bw.Write([byte]$wByte)      # height
    $bw.Write([byte]0)           # color palette
    $bw.Write([byte]0)           # reserved
    $bw.Write([UInt16]1)         # color planes
    $bw.Write([UInt16]32)        # bits per pixel
    $bw.Write([UInt32]$data.Length)
    $bw.Write([UInt32]$offset)
    $offset += $data.Length
}
foreach ($s in $sizes) {
    [byte[]]$data = $pngs[$s]
    $bw.Write($data)
}
$bw.Flush(); $bw.Close(); $fs.Close()

Write-Output "Wrote $outPath"
