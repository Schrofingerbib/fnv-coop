# One copy on the second PC: install NVCoopLab.esp, boot, check it loads and that coc reaches the lab. Started by task.ps1.
$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
$env:PYTHONIOENCODING = "utf-8"
$env:NVCOOP_ADAPTER = "NVIDIA GeForce RTX 3070"
if (Test-Path C:\fnv\lab_smoke_done.txt) { Remove-Item C:\fnv\lab_smoke_done.txt -Force }
& $py -I tools\lab\esp\install_lab.py --instance-dir C:\fnv\fnv-instances\0 *> C:\fnv\lab_smoke_out.txt
& $py -I tools\runner\run.py up --n 1 --wait-pipe 240 *>> C:\fnv\lab_smoke_out.txt
& $py -I tools\lab\smoke_plugin.py --instance 0 *>> C:\fnv\lab_smoke_out.txt
& $py -I tools\runner\run.py down *>> C:\fnv\lab_smoke_out.txt
"done " + (Get-Date) | Set-Content C:\fnv\lab_smoke_done.txt
