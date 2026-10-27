param([string]$Script, [int]$Wait = 5)
# Run a script in the logged-in user's desktop session (a game window needs one; an ssh session has none), then remove the task.
$tn = "nvcoop_task_" + [IO.Path]::GetFileNameWithoutExtension($Script)
schtasks /Create /TN $tn /TR "powershell -NoProfile -ExecutionPolicy Bypass -File $Script" /SC ONCE /ST 23:59 /RU $env:USERNAME /IT /F | Out-Null
schtasks /Run /TN $tn | Out-Null
Start-Sleep -Seconds $Wait
schtasks /Delete /TN $tn /F | Out-Null
"launched $Script"
