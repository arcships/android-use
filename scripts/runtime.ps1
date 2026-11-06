$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = if ($env:ANDROID_USE_RUNTIME_DIR) { $env:ANDROID_USE_RUNTIME_DIR } else { Join-Path $HOME '.local/share/android-use/runtime' }
function Start-AndroidUse {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $root 'launcher-runtime/android-use-runtime.exe'
    $start.Arguments = '--root "' + (Join-Path $root '.') + '" auto-serve'
    $start.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($start)
    $process.WaitForExit()
    exit $process.ExitCode
}
if ((Test-Path (Join-Path $root 'launcher-runtime/android-use-runtime.exe')) -and (Test-Path (Join-Path $root 'current.path'))) {
    Start-AndroidUse
}
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') { throw 'Unsupported runtime architecture' }
$base = 'https://github.com/arcships/android-use/releases/latest/download'
$name = 'android-use-runtime-windows-x86_64.zip'
$work = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    if ($env:ANDROID_USE_GITHUB_TOKEN) {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'Private bootstrap requires GitHub CLI, or offline runtime installation' }
        $previousToken = $env:GH_TOKEN
        try {
            $env:GH_TOKEN = $env:ANDROID_USE_GITHUB_TOKEN
            $downloadOutput = & gh release download --repo arcships/android-use --pattern SHA256SUMS --pattern $name --dir $work
            if ($downloadOutput) { [Console]::Error.WriteLine(($downloadOutput -join [Environment]::NewLine)) }
            if ($LASTEXITCODE -ne 0) { throw 'Private runtime download failed' }
        } finally { $env:GH_TOKEN = $previousToken }
    } else {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 "$base/SHA256SUMS" -OutFile (Join-Path $work 'SHA256SUMS')
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 "$base/$name" -OutFile (Join-Path $work $name)
    }
    $lines = @(Get-Content (Join-Path $work 'SHA256SUMS') | Where-Object { $_ -match "^[a-fA-F0-9]{64}\s+\*?$([regex]::Escape($name))$" })
    if ($lines.Count -ne 1) { throw 'Missing runtime checksum' }
    $expected = ($lines[0] -split '\s+')[0].ToLowerInvariant()
    # Use .NET rather than optional functions/modules inherited from a pwsh host.
    $stream = [System.IO.File]::OpenRead((Join-Path $work $name))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $actual = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally { $stream.Dispose(); $sha.Dispose() }
    if ($actual -ne $expected) { throw 'Runtime checksum mismatch' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $work $name), (Join-Path $work 'bundle'))
    $result = & (Join-Path $work 'bundle/app/android-use-runtime.exe') --root $root install --archive (Join-Path $work $name) --sha256 $expected
    [Console]::Error.WriteLine(($result -join [Environment]::NewLine))
    if ($LASTEXITCODE -ne 0) { throw 'Runtime installation failed' }
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force
}
Start-AndroidUse
