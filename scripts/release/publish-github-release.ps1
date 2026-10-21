param([string]$Repository = 'ShuaiJustin/VidFlow')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $projectRoot
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'Install GitHub CLI and run gh auth login, then retry. You can also run the Release workflow from GitHub Actions.'
}
$releaseVersion = (Get-Content -LiteralPath (Join-Path $projectRoot 'package.json') -Raw | ConvertFrom-Json).version
$releaseTag = "v$releaseVersion"
if ($releaseVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Release requires a stable package.json version.' }
git ls-remote --exit-code --tags origin "refs/tags/$releaseTag" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Push the release commit and tag $releaseTag before starting the workflow." }
gh workflow run release.yml --repo $Repository --ref $releaseTag --field "tag=$releaseTag"
if ($LASTEXITCODE -ne 0) { throw 'Could not start the Release workflow. Check GitHub authentication and workflow availability.' }
Write-Host "Release build started: https://github.com/$Repository/actions/workflows/release.yml"
Write-Host 'The workflow builds Windows x64, macOS arm64 and macOS x64, signs update metadata and publishes the complete release.'
