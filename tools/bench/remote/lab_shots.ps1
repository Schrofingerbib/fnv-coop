# One copy on the second PC: boot with the in-game frame grab on, take stills and an orbit of the lab cell. Started by task.ps1.
$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
$env:PYTHONIOENCODING = "utf-8"
$env:NVCOOP_ADAPTER = "NVIDIA GeForce RTX 3070"
if (Test-Path C:\fnv\lab_shots_done.txt) { Remove-Item C:\fnv\lab_shots_done.txt -Force }
$ini = "C:\fnv\fnv-instances\0\Data\NVSE\Plugins\nvcoop.ini"
$t = Get-Content $ini -Raw
if ($t -notmatch "screenshotD3D9") { $t = $t -replace "\[bridge\]\r?\n", "[bridge]`r`nscreenshotD3D9=1`r`n"; [IO.File]::WriteAllText($ini, $t) }
& $py -I tools\lab\esp\install_lab.py --instance-dir C:\fnv\fnv-instances\0 *> C:\fnv\lab_shots_out.txt
& $py -I tools\runner\run.py up --n 1 --wait-pipe 240 *>> C:\fnv\lab_shots_out.txt
& $py -I tools\lab\shoot_arena.py --instance 0 *>> C:\fnv\lab_shots_out.txt
& $py -I tools\runner\run.py down *>> C:\fnv\lab_shots_out.txt
$t = Get-Content $ini -Raw
[IO.File]::WriteAllText($ini, ($t -replace "screenshotD3D9=1\r?\n", ""))
"done " + (Get-Date) | Set-Content C:\fnv\lab_shots_done.txt
