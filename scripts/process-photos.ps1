# 把 raw/photos/<slug>/ 的原始照片縮成網頁尺寸、移除 EXIF（含 GPS 位置），輸出到 assets/photos/<slug>/
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/process-photos.ps1 <slug>
#       處理這間店所有還沒處理過的照片
#   powershell -ExecutionPolicy Bypass -File scripts/process-photos.ps1 <slug> -Photo omelet.jpg -Crop "0,0.3,1,0.7"
#       只處理一張，並先裁切。Crop = 左,上,寬,高，用 0～1 的比例表示（以轉正後的原圖為準）
#   加上 -Force 才會覆蓋 assets/photos 裡已經存在的同名檔案
#
# 輸出：檔名英文小寫、空白改連字號、副檔名 .jpg；最長邊 1200px；JPEG 品質 82。
# 已存在的輸出檔預設跳過，避免裁切過（例如裁掉人臉）的照片被原圖覆蓋。
param(
    [Parameter(Mandatory = $true)][string]$Slug,
    [string]$Photo = "",
    [string]$Crop = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "raw/photos/$Slug"
$dst = Join-Path $root "assets/photos/$Slug"
if (-not (Test-Path $src)) { throw "找不到 $src" }
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$cropBox = $null
if ($Crop) {
    if (-not $Photo) { throw "使用 -Crop 時必須用 -Photo 指定一張照片" }
    $cropBox = $Crop.Split(",") | ForEach-Object { [double]$_.Trim() }
    if ($cropBox.Count -ne 4) { throw "-Crop 格式是「左,上,寬,高」，例如 0,0.3,1,0.7" }
    if (($cropBox | Where-Object { $_ -lt 0 -or $_ -gt 1 }).Count -gt 0 -or ($cropBox[0] + $cropBox[2]) -gt 1.0001 -or ($cropBox[1] + $cropBox[3]) -gt 1.0001) {
        throw "-Crop 的數值必須介於 0～1，而且裁切範圍不能超出照片"
    }
}

$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
$params = New-Object System.Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), 82L
$maxSide = 1200

$files = Get-ChildItem $src -File | Where-Object { $_.Extension -match '^\.(jpe?g|png)$' }
if ($Photo) { $files = $files | Where-Object { $_.Name -eq $Photo }; if (-not $files) { throw "在 $src 找不到 $Photo" } }

foreach ($f in $files) {
    $name = ($f.BaseName.ToLower() -replace '[^a-z0-9]+', '-').Trim('-') + ".jpg"
    $out = Join-Path $dst $name
    if ((Test-Path $out) -and -not $Force) {
        Write-Output "略過 $($f.Name)：assets/photos/$Slug/$name 已存在（要重新處理請加 -Force）"
        continue
    }
    $img = [System.Drawing.Image]::FromFile($f.FullName)
    try {
        # 依 EXIF 方向轉正（手機直拍的照片）
        if ($img.PropertyIdList -contains 274) {
            switch ($img.GetPropertyItem(274).Value[0]) {
                3 { $img.RotateFlip([System.Drawing.RotateFlipType]::Rotate180FlipNone) }
                6 { $img.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone) }
                8 { $img.RotateFlip([System.Drawing.RotateFlipType]::Rotate270FlipNone) }
            }
        }
        $sx = 0; $sy = 0; $sw = $img.Width; $sh = $img.Height
        if ($cropBox) {
            $sx = [int]($img.Width * $cropBox[0]); $sy = [int]($img.Height * $cropBox[1])
            $sw = [int]($img.Width * $cropBox[2]); $sh = [int]($img.Height * $cropBox[3])
        }
        $scale = [Math]::Min(1.0, $maxSide / [Math]::Max($sw, $sh))
        $w = [int]($sw * $scale); $h = [int]($sh * $scale)
        # 畫到一張全新的點陣圖上：新圖不帶任何 EXIF
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img, (New-Object System.Drawing.Rectangle 0, 0, $w, $h), (New-Object System.Drawing.Rectangle $sx, $sy, $sw, $sh), [System.Drawing.GraphicsUnit]::Pixel)
        $g.Dispose()
        $bmp.Save($out, $codec, $params)
        $bmp.Dispose()
        $note = if ($cropBox) { "，裁切 $Crop" } else { "" }
        Write-Output "OK  $($f.Name) -> assets/photos/$Slug/$name（${w}x${h}$note）"
    } finally { $img.Dispose() }
}
Write-Output "完成。請逐張檢查有沒有可辨識的人臉，再把檔名加進咖啡廳頁的 photos 欄位。"
