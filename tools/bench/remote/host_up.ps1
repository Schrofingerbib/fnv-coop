$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
$env:PYTHONIOENCODING = "utf-8"
$env:NVCOOP_VISIBLE = "1"
$env:NVCOOP_TEST_HELPERS = "1"
$env:NVCOOP_LEAN = "0"
& $py -I tools\runner\run.py up --n 1 --wait-pipe 180 *> C:\fnv\up_out.txt
