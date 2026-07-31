param(
    [Parameter(Mandatory = $true)]
    [string]$PdfPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [uint32]$Width = 1275
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
$null = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType=WindowsRuntime]

$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$genericAsTask = $methods |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1
    } |
    Select-Object -First 1
$actionAsTask = $methods |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        -not $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1
    } |
    Select-Object -First 1

function Wait-WinRtOperation {
    param($Operation, [Type]$ResultType)
    $method = $genericAsTask.MakeGenericMethod($ResultType)
    $task = $method.Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

function Wait-WinRtAction {
    param($Action)
    $task = $actionAsTask.Invoke($null, @($Action))
    $task.Wait()
}

$pdfResolved = (Resolve-Path -LiteralPath $PdfPath).Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputResolved = (Resolve-Path -LiteralPath $OutputDirectory).Path
$storageFile = Wait-WinRtOperation (
    [Windows.Storage.StorageFile]::GetFileFromPathAsync($pdfResolved)
) ([Windows.Storage.StorageFile])
$document = Wait-WinRtOperation (
    [Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($storageFile)
) ([Windows.Data.Pdf.PdfDocument])

for ($index = 0; $index -lt $document.PageCount; $index++) {
    $page = $document.GetPage($index)
    try {
        $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        try {
            $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
            $options.DestinationWidth = $Width
            Wait-WinRtAction ($page.RenderToStreamAsync($stream, $options))
            $stream.Seek(0)
            $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead(
                $stream
            )
            $path = Join-Path $outputResolved (
                'pagina-{0:D2}.png' -f ($index + 1)
            )
            $fileStream = [System.IO.File]::Create($path)
            try {
                $netStream.CopyTo($fileStream)
            }
            finally {
                $fileStream.Dispose()
                $netStream.Dispose()
            }
        }
        finally {
            $stream.Dispose()
        }
    }
    finally {
        $page.Dispose()
    }
}

Write-Output "PAGES=$($document.PageCount)"
Write-Output "OUTPUT=$outputResolved"
