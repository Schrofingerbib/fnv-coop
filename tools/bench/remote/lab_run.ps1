# The lab run (docs/tools/lab.md) on the second PC, started in the logged-in desktop session by task.ps1:
#   task.ps1 -Script C:/fnv/fnv-coop/tools/bench/remote/lab_run.ps1 -Wait 5
# Three pairs at once (6 copies in C:\fnv\fnv-instances). stdout = the summary, stderr = progress; the files are out\lab\<stamp>\results.json and summary.md.
# C:\fnv\lab_done.txt appears when the run ends (exit code inside). Needs runs\gameslot_config.json {"slots": 3}.
param([int]$Pairs = 3, [int]$BudgetMin = 20, [string]$Rooms = "")
$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
$env:PYTHONIOENCODING = "utf-8"
$env:NVCOOP_LEAN = "0"
$env:NVCOOP_ADAPTER = "NVIDIA GeForce RTX 3070"          # an ssh/task session cannot enumerate display adapters
$env:PROOF_MAX_PAIRS = "$Pairs"
$env:PROOF_NVSE_DIR = "C:\fnv\fnv-coop\out\cache\deps\nvse"
if (Test-Path C:\fnv\lab_done.txt) { Remove-Item C:\fnv\lab_done.txt -Force }
$labArgs = @("-I", "tools\lab\lab.py", "run", "--pairs", "$Pairs", "--budget-min", "$BudgetMin", "--reserve-gb", "2")
if ($Rooms -ne "") { $labArgs += @("--rooms", $Rooms) }
& $py @labArgs 1> C:\fnv\lab_out.txt 2> C:\fnv\lab_err.txt
"done rc=$LASTEXITCODE " + (Get-Date) | Set-Content C:\fnv\lab_done.txt
