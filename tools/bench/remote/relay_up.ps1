$py = "C:\Program Files\Python311\python.exe"
Set-Location C:\fnv\fnv-coop
& $py -I tools\runner\pipe_relay.py --instance 0 --port 27971 *> C:\fnv\relay_out.txt
