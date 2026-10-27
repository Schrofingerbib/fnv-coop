$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'pipe_relay\.py' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "stopped relay " + $_.ProcessId }
& $py -I tools\runner\run.py down 2>&1 | Select-Object -Last 3
if (Test-Path C:\fnv\up_out.txt) { Remove-Item C:\fnv\up_out.txt -Force }
