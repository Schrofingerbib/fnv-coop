$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
$env:PYTHONIOENCODING = "utf-8"
$env:NVCOOP_LEAN = "0"
if (Test-Path C:\fnv\bench_done.txt) { Remove-Item C:\fnv\bench_done.txt -Force }
& $py -I tools\bench\coop_bench.py --scenario standard_nonet --label two-copies-on-dino-pc *> C:\fnv\bench_out.txt
"done " + (Get-Date) | Set-Content C:\fnv\bench_done.txt

