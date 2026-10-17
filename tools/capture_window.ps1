# Capture a window by title without stealing focus (PrintWindow + PW_RENDERFULLCONTENT).
# Usage: .\tools\capture_window.ps1 -TitleLike "*wc3studio*" -Out "build\gui.png"
param(
    [string]$TitleLike = "*wc3studio*",
    [string]$Out = "build\gui.png"
)
Add-Type -AssemblyName System.Drawing
$code = @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public class WinCap {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static Bitmap Capture(IntPtr h) {
    RECT r; GetWindowRect(h, out r);
    int w = r.R - r.L, ht = r.B - r.T;
    if (w <= 0 || ht <= 0) return null;
    Bitmap bmp = new Bitmap(w, ht);
    using (Graphics g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc();
      PrintWindow(h, dc, 2);   // 2 = PW_RENDERFULLCONTENT
      g.ReleaseHdc(dc);
    }
    return bmp;
  }
}
'@
Add-Type -TypeDefinition $code -ReferencedAssemblies System.Drawing

$proc = Get-Process | Where-Object { $_.MainWindowTitle -like $TitleLike -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { Write-Output "window not found"; exit 1 }
Write-Output "capturing pid $($proc.Id): $($proc.MainWindowTitle)"
$bmp = [WinCap]::Capture($proc.MainWindowHandle)
if (-not $bmp) { Write-Output "capture failed"; exit 1 }
$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output "saved $Out  $($bmp.Width)x$($bmp.Height)"
$bmp.Dispose()
