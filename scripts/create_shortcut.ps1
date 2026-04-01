# ============================================================
#  BGU Lecture Summarizer — Create icon + desktop shortcut
# ============================================================
Add-Type -AssemblyName System.Drawing

$PROJECT = "C:\Users\royan\IdeaProjects\lecture-summarizer"
$ICO_PATH = "$PROJECT\assets\bgu_ai.ico"
$BAT_PATH = "$PROJECT\run_BGU_AI.bat"
$DESKTOP  = [Environment]::GetFolderPath("Desktop")
$LNK_PATH = "$DESKTOP\BGU Lecture Summarizer.lnk"

# ── 1. Build 48x48 icon ───────────────────────────────────────────────────────
$SIZE = 48
$bmp = New-Object System.Drawing.Bitmap($SIZE, $SIZE,
       [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode    = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint= [System.Drawing.Text.TextRenderingHint]::AntiAlias

# Purple → blue diagonal gradient
$gradBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    [System.Drawing.Point]::new(0, 0),
    [System.Drawing.Point]::new($SIZE, $SIZE),
    [System.Drawing.Color]::FromArgb(255, 107, 89, 230),
    [System.Drawing.Color]::FromArgb(255, 96, 165, 250)
)

# Rounded-rectangle clip path (radius 10)
$rr = New-Object System.Drawing.Drawing2D.GraphicsPath
$r  = 10
$rr.AddArc(0,          0,          $r*2, $r*2, 180, 90)
$rr.AddArc($SIZE-$r*2, 0,          $r*2, $r*2, 270, 90)
$rr.AddArc($SIZE-$r*2, $SIZE-$r*2, $r*2, $r*2,   0, 90)
$rr.AddArc(0,          $SIZE-$r*2, $r*2, $r*2,  90, 90)
$rr.CloseFigure()
$g.FillPath($gradBrush, $rr)

# Graduation-cap emoji centred in the tile
try {
    $font = New-Object System.Drawing.Font(
        "Segoe UI Emoji", 28,
        [System.Drawing.FontStyle]::Regular,
        [System.Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object System.Drawing.StringFormat
    $sf.Alignment     = [System.Drawing.StringAlignment]::Center
    $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
    $rect = New-Object System.Drawing.RectangleF(0, 0, $SIZE, $SIZE)
    $g.DrawString([char]::ConvertFromUtf32(0x1F393), $font,
                  [System.Drawing.Brushes]::White, $rect, $sf)
    $font.Dispose()
} catch {
    # Fallback: plain white mortarboard shape
    $wb = [System.Drawing.Brushes]::White
    # diamond (cap board)
    $pts = @(
        [System.Drawing.Point]::new(24, 10),
        [System.Drawing.Point]::new(40, 20),
        [System.Drawing.Point]::new(24, 30),
        [System.Drawing.Point]::new( 8, 20)
    )
    $g.FillPolygon($wb, $pts)
    # stem
    $g.FillRectangle($wb, 21, 29, 6, 9)
    # tassel dot
    $g.FillEllipse(
        (New-Object System.Drawing.SolidBrush(
            [System.Drawing.Color]::FromArgb(255,253,224,71))),
        20, 37, 8, 8)
}

$g.Dispose()
$gradBrush.Dispose()
$rr.Dispose()

# ── 2. Wrap bitmap as PNG inside a proper ICO container ──────────────────────
$pngMs = New-Object System.IO.MemoryStream
$bmp.Save($pngMs, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
$png = $pngMs.ToArray()
$pngMs.Dispose()

$ico = New-Object System.Collections.Generic.List[byte]

# ICONDIR (6 bytes)
$ico.AddRange([byte[]](0, 0, 1, 0, 1, 0))

# ICONDIRENTRY (16 bytes)
$ico.Add([byte]$SIZE)          # width
$ico.Add([byte]$SIZE)          # height
$ico.Add(0)                    # color count  (0 = truecolor)
$ico.Add(0)                    # reserved
$ico.AddRange([byte[]](1, 0))  # planes = 1
$ico.AddRange([byte[]](32, 0)) # bits per pixel = 32
# PNG byte-length (4 bytes LE)
$len = $png.Length
$ico.Add([byte]($len          -band 0xFF))
$ico.Add([byte](($len -shr  8)-band 0xFF))
$ico.Add([byte](($len -shr 16)-band 0xFF))
$ico.Add([byte](($len -shr 24)-band 0xFF))
# Image offset = 6 (ICONDIR) + 16 (ICONDIRENTRY) = 22  (4 bytes LE)
$ico.AddRange([byte[]](22, 0, 0, 0))

$ico.AddRange($png)
[System.IO.File]::WriteAllBytes($ICO_PATH, $ico.ToArray())
Write-Host "✅ Icon saved  →  $ICO_PATH"

# ── 3. Create desktop shortcut ───────────────────────────────────────────────
$shell = New-Object -ComObject WScript.Shell
$sc    = $shell.CreateShortcut($LNK_PATH)
$sc.TargetPath       = $BAT_PATH
$sc.WorkingDirectory = $PROJECT
$sc.IconLocation     = "$ICO_PATH,0"
$sc.Description      = "BGU Lecture Summarizer - AI Powered"
$sc.WindowStyle      = 1   # normal window
$sc.Save()
Write-Host "✅ Shortcut created  →  $LNK_PATH"
Write-Host ""
Write-Host "Double-click BGU Lecture Summarizer on your desktop to launch the app."
