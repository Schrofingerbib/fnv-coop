# Bisect the plugin features against game speed on instance 0. Each config = minimal nvcoop.ini + extras; fresh launch, load, clear boxes, measure.
# Metrics (from the session logger, read-only): plugin frame counter fps, game CPU %, menu state. The original ini is restored at the end.
$root = Split-Path (Split-Path $PSScriptRoot)
Set-Location $root
$ini = Join-Path (Split-Path $root) 'fnv-instances\0\Data\NVSE\Plugins\nvcoop.ini'
Copy-Item $ini "$ini.bisect.bak" -Force
$head = @"
[bridge]
instance=0
autoload=
minimized=0
focusGuard=1
multiInstance=1

[loader]
core=nvcoop_mod.dll

"@
$coop = "[coop]`r`ninstanceId=0`r`nrole=host`r`npipe=\\.\pipe\nvcoop-0`r`nenable=1`r`n`r`n"
$coop0 = $coop -replace 'enable=1', 'enable=0'
$bots = "[dev]`r`nbots=1`r`n`r`n"
$cfgs = [ordered]@{
    'A bots only (coop off)'      = $head + $coop0 + $bots
    'B coop on + bots'            = $head + $coop + $bots
    'C B + live.actors'           = $head + $coop + $bots + "[live]`r`nactors=1`r`n`r`n"
    'D B + live.avatars'          = $head + $coop + $bots + "[live]`r`navatars=1`r`n`r`n[avatar]`r`nenabled=1`r`n`r`n"
    'E B + live.combat'           = $head + $coop + $bots + "[live]`r`ncombat=1`r`n`r`n[combat]`r`nenabled=1`r`ngate=1`r`nnative=1`r`n`r`n"
    'F B + live.spawns'           = $head + $coop + $bots + "[live]`r`nspawns=1`r`n`r`n"
    'G B + anim + animprobe'      = $head + $coop + "[dev]`r`nbots=1`r`nanimprobe=1`r`n`r`n[anim]`r`nenabled=1`r`n`r`n"
}
$env:NVCOOP_VISIBLE = '1'; $env:NVCOOP_TEST_HELPERS = '1'; $env:PYTHONIOENCODING = 'utf-8'
$out = @()
try {
    foreach ($name in $cfgs.Keys) {
        python -I tools/runner/run.py down 2>&1 | Out-Null; Start-Sleep 25
        Set-Content $ini $cfgs[$name]
        python -I tools/runner/run.py up --n 1 --wait-pipe 180 2>&1 | Out-Null
        python -I -c "
import sys;sys.path[:0]=['tools','tools/runner']
from nvbridge import NvBridge
from bots.bot import Bot
n=NvBridge.connect(instance=0,wait_s=10)
n.call('loadSave',{'name':'nvcoop_autosave_ready','timeoutMs':120000},timeout_s=130)
Bot(n,name='b0',slot=0).dismiss_messages(quiet_s=25,max_s=150,max_n=20)
" 2>&1 | Out-Null
        Start-Sleep 50
        $s = (Get-Content out/diag/sessions/LATEST.txt).Trim()
        $rows = Get-Content (Join-Path $s 'timeline.jsonl') | % { $_ | ConvertFrom-Json } | ? { $_.i -eq 0 }
        $last = $rows | ? { $_.t -ge (($rows | select -Last 1).t - 35) }
        $fr = $last | ? { $_.frames -ne $null }
        $fps = if ($fr.Count -ge 2) { [math]::Round(($fr[-1].frames - $fr[0].frames) / ($fr[-1].t - $fr[0].t), 1) } else { 'n/a' }
        $cpu = [math]::Round(($last | ? { $_.cpu_pct -ne $null } | measure cpu_pct -Average).Average)
        $menu = ($last | select -Last 1).menu
        $dt = ($last | select -Last 1).dt_ms
        $line = "{0,-26} fps={1,-6} game_cpu={2}%  menu={3}  dt_ms={4}" -f $name, $fps, $cpu, $menu, $dt
        $out += $line; Write-Output $line
    }
}
finally {
    python -I tools/runner/run.py down 2>&1 | Out-Null
    Copy-Item "$ini.bisect.bak" $ini -Force
    Write-Output 'restored ini'
}
