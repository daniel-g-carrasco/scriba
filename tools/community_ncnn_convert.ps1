param(
    [Parameter(Mandatory=$true)][string]$Pnnx,
    [Parameter(Mandatory=$true)][string]$Models,
    [Parameter(Mandatory=$true)][string]$Out
)
# Convert copies only: pnnx writes several files beside its input.
$ErrorActionPreference='Stop'
if (Test-Path -LiteralPath $Out) { throw 'Output directory must be new' }
New-Item -ItemType Directory -Path $Out | Out-Null
foreach ($name in @('segmentation','embedding')) {
    $target=Join-Path $Out ($name+'.onnx')
    Copy-Item -LiteralPath (Join-Path $Models ($name+'.onnx')) -Destination $target
    $shape=if ($name -eq 'segmentation') { 'inputshape=[1,1,160000]' } else { 'inputshape=[1,998,80],[1,3,589]' }
    $previousPreference=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    & $Pnnx $target $shape 'fp16=0' *> (Join-Path $Out ($name+'-convert.log'))
    $code=$LASTEXITCODE
    $ErrorActionPreference=$previousPreference
    Add-Content -LiteralPath (Join-Path $Out 'status.txt') -Value "$name exit $code"
}
