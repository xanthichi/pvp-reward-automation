param(
    [string]$Message = "chore: update",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Owner = "xanthichi"
$Repository = "pvp-reward-automation"
$PublicAuthorName = "xanthichi"
$PublicAuthorEmail = "135406843+xanthichi@users.noreply.github.com"

Set-Location $Root

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed. Nothing was pushed and the local app is still running."
}

node --check app\static\app.js
if ($LASTEXITCODE -ne 0) {
    throw "JavaScript validation failed. Nothing was pushed."
}

$changes = @(git status --porcelain)
if ($changes.Count -gt 0) {
    git add -A
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) {
        throw "Git commit failed. Nothing was pushed."
    }
}

$credentialText = "protocol=https`nhost=github.com`n`n" | git credential fill
$passwordLine = $credentialText |
    Where-Object { $_ -like "password=*" } |
    Select-Object -First 1
if (-not $passwordLine) {
    throw "GitHub credentials are not available."
}
$Token = $passwordLine.Substring(9)
$Headers = @{
    Authorization = "Bearer $Token"
    Accept = "application/vnd.github+json"
    "User-Agent" = "Codex-PVP-Publish"
}
$Api = "https://api.github.com/repos/$Owner/$Repository"

$Ref = Invoke-RestMethod -Uri "$Api/git/ref/heads/main" `
    -Headers $Headers -TimeoutSec 30
$RemoteCommit = Invoke-RestMethod `
    -Uri "$Api/git/commits/$($Ref.object.sha)" `
    -Headers $Headers -TimeoutSec 30
$LocalTree = (git write-tree).Trim()

if ($RemoteCommit.tree.sha -ne $LocalTree) {
    $LocalFiles = @(git ls-files)
    $RemoteTree = Invoke-RestMethod `
        -Uri "$Api/git/trees/$($RemoteCommit.tree.sha)?recursive=1" `
        -Headers $Headers -TimeoutSec 60
    $RemoteFiles = @(
        $RemoteTree.tree |
            Where-Object { $_.type -eq "blob" } |
            ForEach-Object { $_.path }
    )

    $TreeItems = @()
    foreach ($Path in $LocalFiles) {
        $Content = Get-Content -LiteralPath $Path -Raw -Encoding utf8
        $TreeItems += [ordered]@{
            path = $Path
            mode = "100644"
            type = "blob"
            content = $Content
        }
    }
    foreach ($Path in $RemoteFiles) {
        if ($LocalFiles -notcontains $Path) {
            $TreeItems += [ordered]@{
                path = $Path
                mode = "100644"
                type = "blob"
                sha = $null
            }
        }
    }

    $TreeBody = [ordered]@{
        base_tree = $RemoteCommit.tree.sha
        tree = $TreeItems
    } | ConvertTo-Json -Depth 8 -Compress
    $Tree = Invoke-RestMethod -Uri "$Api/git/trees" `
        -Headers $Headers -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes($TreeBody)) `
        -TimeoutSec 120

    $CommitBody = [ordered]@{
        message = $Message
        tree = $Tree.sha
        parents = @($Ref.object.sha)
        author = [ordered]@{
            name = $PublicAuthorName
            email = $PublicAuthorEmail
            date = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
        committer = [ordered]@{
            name = $PublicAuthorName
            email = $PublicAuthorEmail
            date = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
    } | ConvertTo-Json -Depth 5 -Compress
    $Commit = Invoke-RestMethod -Uri "$Api/git/commits" `
        -Headers $Headers -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes($CommitBody)) `
        -TimeoutSec 60

    $RefBody = @{
        sha = $Commit.sha
        force = $false
    } | ConvertTo-Json -Compress
    $UpdatedRef = Invoke-RestMethod -Uri "$Api/git/refs/heads/main" `
        -Headers $Headers -Method Patch `
        -ContentType "application/json" `
        -Body ([Text.Encoding]::UTF8.GetBytes($RefBody)) `
        -TimeoutSec 60
    Write-Host "Pushed commit: $($UpdatedRef.object.sha)"
} else {
    Write-Host "Remote tree already matches local files."
}

$Listeners = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen `
        -ErrorAction SilentlyContinue
)
foreach ($Listener in $Listeners) {
    Stop-Process -Id $Listener.OwningProcess -Force -ErrorAction SilentlyContinue
}

Write-Host "Repository: https://github.com/$Owner/$Repository"
Write-Host "Local app on port $Port stopped."

