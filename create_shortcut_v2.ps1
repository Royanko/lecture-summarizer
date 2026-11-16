# create_shortcut_v2.ps1
# Creates the new desktop shortcut for the Next.js + FastAPI app.
# Run once:  powershell -ExecutionPolicy Bypass -File create_shortcut_v2.ps1

$ProjectDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatPath      = Join-Path $ProjectDir "start_app.bat"
$IconFile     = Join-Path $ProjectDir "assets\bgu_ai.ico"
$Desktop      = [Environment]::GetFolderPath("Desktop")

$OldShortcut  = Join-Path $Desktop "Lecture Summarizer.lnk"
$NewShortcut  = Join-Path $Desktop "Lecture Summarizer.lnk"

# ── 1. Delete old shortcut if it points to the legacy batch file ───────────────
if (Test-Path $OldShortcut) {
    $shell   = New-Object -ComObject WScript.Shell
    $existing = $shell.CreateShortcut($OldShortcut)
    if ($existing.TargetPath -like "*run_BGU_AI*" -or
        $existing.TargetPath -like "*streamlit*"  -or
        $existing.TargetPath -like "*run_app.bat*") {
        Remove-Item $OldShortcut -Force
        Write-Host "Deleted legacy shortcut: $OldShortcut" -ForegroundColor Yellow
    } else {
        Write-Host "Existing shortcut is not legacy — overwriting anyway." -ForegroundColor Cyan
        Remove-Item $OldShortcut -Force
    }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
}

# ── 2. Build new shortcut pointing to start_app.bat ──────────────────────────
$shell2   = New-Object -ComObject WScript.Shell
$sc       = $shell2.CreateShortcut($NewShortcut)
$sc.TargetPath       = $BatPath
$sc.WorkingDirectory = $ProjectDir
$sc.IconLocation     = "$IconFile,0"
$sc.Description      = "Lecture Summarizer v2.0 -- Next.js + FastAPI + Gemini"
$sc.WindowStyle      = 1   # 1 = normal window
$sc.Save()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell2) | Out-Null

Write-Host "New shortcut created: $NewShortcut" -ForegroundColor Green
Write-Host "  Target : $BatPath"
Write-Host "  Icon   : $IconFile"
