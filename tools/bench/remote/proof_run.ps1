# The in-game proof matrix on the second PC, started in the logged-in desktop session by task.ps1:
#   task.ps1 -Script C:/fnv/fnv-coop/tools/bench/remote/proof_run.ps1 -Wait 5
# Three pairs at once (6 copies in C:\fnv\fnv-instances). stdout = one verdict json per test, stderr = progress.
# C:\fnv\proof_done.txt appears when the queue ends (exit code inside). Needs runs\gameslot_config.json {"slots": 3}.
param([int]$Pairs = 3, [string]$Round = "dino1")
$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
$env:PYTHONIOENCODING = "utf-8"
$env:NVCOOP_LEAN = "0"
$env:NVCOOP_ADAPTER = "NVIDIA GeForce RTX 3070"          # an ssh/task session cannot enumerate display adapters
$env:PROOF_MAX_PAIRS = "$Pairs"
$env:PROOF_NVSE_DIR = "C:\fnv\fnv-coop\out\cache\deps\nvse"
if (Test-Path C:\fnv\proof_done.txt) { Remove-Item C:\fnv\proof_done.txt -Force }
& $py -I tools\proof\prove.py queue --pairs $Pairs --runs 1 --reserve-gb 2 --round $Round 1> C:\fnv\proof_out.txt 2> C:\fnv\proof_err.txt
"done rc=$LASTEXITCODE " + (Get-Date) | Set-Content C:\fnv\proof_done.txt
