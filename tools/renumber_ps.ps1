$clipsDir = "D:\Video-pipeline-library\library_cosmos\clips"
$all = [System.IO.Directory]::GetFiles($clipsDir, "*.mp4")

# Separate into groups
$numeric = @()
$tmp     = @()
$pool3   = @()

foreach ($path in $all) {
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($path)
    if ($stem -match "^(recovery_tmp_|__tmp_)(\d+)$") {
        $tmp += [PSCustomObject]@{ Num = [int]$Matches[2]; Path = $path }
    } elseif ($stem -match "^pool3_(\d+)$") {
        $pool3 += [PSCustomObject]@{ Num = [int]$Matches[1]; Path = $path }
    } elseif ($stem -match "^(\d+)$") {
        $numeric += [PSCustomObject]@{ Num = [int]$Matches[1]; Path = $path }
    }
}

$numeric = $numeric | Sort-Object Num
$tmp     = $tmp     | Sort-Object Num
$pool3   = $pool3   | Sort-Object Num

$ordered = @($numeric) + @($tmp) + @($pool3)
$total = $ordered.Count
Write-Host "Total: $total (numeric=$($numeric.Count) tmp=$($tmp.Count) pool3=$($pool3.Count))"

# Phase 1: rename to PHASE1_XXXX.mp4 using File.Move
Write-Host "Phase 1: renaming to PHASE1_XXXX..."
for ($i = 0; $i -lt $ordered.Count; $i++) {
    $idx = $i + 1
    $newPath = [System.IO.Path]::Combine($clipsDir, ("PHASE1_{0:D4}.mp4" -f $idx))
    [System.IO.File]::Move($ordered[$i].Path, $newPath)
    if ($idx % 1000 -eq 0) { Write-Host "  Phase1: [$idx/$total]" }
}
Write-Host "Phase 1 done."

# Phase 2: rename PHASE1_XXXX -> XXXX.mp4
Write-Host "Phase 2: final numbering..."
$phase1files = [System.IO.Directory]::GetFiles($clipsDir, "PHASE1_*.mp4") | Sort-Object
for ($i = 0; $i -lt $phase1files.Count; $i++) {
    $idx = $i + 1
    $newPath = [System.IO.Path]::Combine($clipsDir, ("{0:D4}.mp4" -f $idx))
    [System.IO.File]::Move($phase1files[$i], $newPath)
    if ($idx % 1000 -eq 0) { Write-Host "  Phase2: [$idx/$total]" }
}
Write-Host "Phase 2 done."

$result = [System.IO.Directory]::GetFiles($clipsDir, "*.mp4")
Write-Host ("DONE: {0} clips, 0001.mp4 to {1:D4}.mp4" -f $result.Count, $result.Count)
