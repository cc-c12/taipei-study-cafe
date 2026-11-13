# 把 raw/photos/<slug>/ 的原始照片縮成網頁尺寸，並移除 EXIF（含 GPS 位置），輸出到 assets/photos/<slug>/
# 用法：powershell -ExecutionPolicy Bypass -File scripts/process-photos.ps1 <咖啡廳slug>
# 輸出檔名：英文小寫，空白改連字號，副檔名一律 .jpg；最長邊 1200px，JPEG 品質 82。
param([Parameter(Mandatory = $true)][string]$Slug)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "raw/photos/$Slug"
$dst = Join-Path $root "assets/photos/$Slug"
if (-not (Test-Path $src)) { throw "找不到 $src" }
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
$params = New-Object System.Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), 82L
$maxSide = 1200

Get-ChildItem $src -File | Where-Object { $_.Extension -match '^\.(jpe?g|png)$' } | ForEach-Object {
    $img = [System.Drawing.Image]::FromFile($_.FullName)
    try {
        # 依 EXIF 方向轉正（手機直拍的照片）
        if ($img.PropertyIdList -contains 274) {
            switch ($img.GetPropertyItem(274).Value[0]) {
                3 { $img.RotateFlip([System.Drawing.RotateFlipType]::Rotate180FlipNone) }
                6 { $img.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone) }
                8 { $img.RotateFlip([System.Drawing.RotateFlipType]::Rotate270FlipNone) }
            }
        }
        $scale = [Math]::Min(1.0, $maxSide / [Math]::Max($img.Width, $img.Height))
        $w = [int]($img.Width * $scale); $h = [int]($img.Height * $scale)
        # 畫到一張全新的點陣圖上：新圖不帶任何 EXIF
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img, 0, 0, $w, $h)
        $g.Dispose()
        $name = ($_.BaseName.ToLower() -replace '[^a-z0-9]+', '-').Trim('-') + ".jpg"
        $bmp.Save((Join-Path $dst $name), $codec, $params)
        $bmp.Dispose()
        Write-Output "OK  $($_.Name) -> assets/photos/$Slug/$name（${w}x${h}）"
    } finally { $img.Dispose() }
}
Write-Output "完成。請逐張檢查有沒有可辨識的人臉，再把檔名加進咖啡廳頁的 photos 欄位。"
