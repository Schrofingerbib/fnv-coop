param([string]$Label, [string[]]$Set = @(), [int]$Seconds = 15, [string]$Save = 'nvcoop_autosave_ready')
# A/B one display setting on instance 0: apply KEY=VALUE pairs to FalloutPrefs.ini (backup restored at the end), relaunch one visible copy,
# load the save, dismiss the DLC boxes, then run tools/diag/live_log.py and print its summary.
$root = Split-Path (Split-Path $PSScriptRoot)
$inst = Join-Path (Split-Path $root) 'fnv-instances\0'
$prefs = Join-Path $inst 'FalloutPrefs.ini'
$bak = "$prefs.ab.bak"
Set-Location $root
python -I tools/runner/run.py down | Out-Null
Start-Sleep 3
Copy-Item $prefs $bak -Force
try {
    foreach ($kv in $Set) {
        $k, $v = $kv -split '=', 2
        $txt = Get-Content $prefs -Raw
        $new = [regex]::Replace($txt, "(?m)^$([regex]::Escape($k))=.*$", "$k=$v")
        if ($new -eq $txt -and $txt -notmatch "(?m)^$([regex]::Escape($k))=$([regex]::Escape($v))\s*$") { Write-Host "WARN key not found: $k" }
        Set-Content $prefs $new -NoNewline
    }
    $env:NVCOOP_VISIBLE = '1'; $env:NVCOOP_TEST_HELPERS = '1'
    python -I tools/runner/run.py up --n 1 --wait-pipe 150 | Out-Null
    python -I -c "
import sys;sys.path[:0]=['tools','tools/runner']
from nvbridge import NvBridge
from bots.bot import Bot
n=NvBridge.connect(instance=0,wait_s=10)
n.call('loadSave',{'name':'$Save','timeoutMs':120000},timeout_s=130)
Bot(n,name='b0',slot=0).dismiss_messages(quiet_s=25,max_s=150,max_n=20)
"
    python -I tools/diag/live_log.py --instance 0 --seconds $Seconds --every 7 --label $Label --out "out/diag/$Label.jsonl" | Select-String 'SUMMARY|engine frame|thread \d+:'
}
finally {
    python -I tools/runner/run.py down | Out-Null
    Copy-Item $bak $prefs -Force
}
