$procs = Get-WmiObject Win32_Process | Where-Object { $_.Name -like 'python*' }
$keywords = @('telegram_bot', 'media_generator', 'prompt_generator', 'Video-pipeline')
$killed = @()

foreach ($p in $procs) {
    $cmd = $p.CommandLine
    foreach ($kw in $keywords) {
        if ($cmd -like "*$kw*") {
            Write-Host "Killing PID=$($p.ProcessId): $cmd"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $killed += $p.ProcessId
            break
        }
    }
}

if ($killed.Count -eq 0) {
    Write-Host "No Video-pipeline processes found."
} else {
    Write-Host "Killed $($killed.Count) process(es): $($killed -join ', ')"
}
