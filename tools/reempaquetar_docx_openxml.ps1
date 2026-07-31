param(
    [string]$DocxPath = (Join-Path $PSScriptRoot '..\docs\interno\arquitectura\Documentacion_Tecnica_Aprobado_Financiacion_Educativa.docx'),
    [string]$MembretePath = (Join-Path $PSScriptRoot '..\MembreteAprobado.jpg')
)

$ErrorActionPreference = 'Stop'
$dll = 'C:\Program Files\Microsoft Office\root\vfs\ProgramFilesCommonX64\Microsoft Shared\Filters\Documentformat.OpenXml.dll'
$source = (Resolve-Path -LiteralPath $DocxPath).Path
$image = (Resolve-Path -LiteralPath $MembretePath).Path
$temporary = [System.IO.Path]::ChangeExtension($source, '.repacked.docx')

Add-Type -Path $dll
Add-Type -ReferencedAssemblies @($dll, 'WindowsBase') -TypeDefinition @'
using System;
using System.IO;
using System.Linq;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

public static class AprobadoDocxRepacker
{
    public static void Repack(string sourcePath, string imagePath, string outputPath)
    {
        if (File.Exists(outputPath))
            File.Delete(outputPath);

        using (WordprocessingDocument source = WordprocessingDocument.Open(sourcePath, false))
        using (WordprocessingDocument target = WordprocessingDocument.Create(
            outputPath,
            WordprocessingDocumentType.Document))
        {
            MainDocumentPart sourceMain = source.MainDocumentPart;
            MainDocumentPart main = target.AddMainDocumentPart();
            main.Document = (Document)sourceMain.Document.CloneNode(true);

            StyleDefinitionsPart styles = main.AddNewPart<StyleDefinitionsPart>("rId3");
            styles.Styles = (Styles)sourceMain.StyleDefinitionsPart.Styles.CloneNode(true);

            DocumentSettingsPart settings = main.AddNewPart<DocumentSettingsPart>("rId4");
            settings.Settings = (Settings)sourceMain.DocumentSettingsPart.Settings.CloneNode(true);

            foreach (HeaderPart sourceHeader in sourceMain.HeaderParts)
            {
                string headerId = sourceMain.GetIdOfPart(sourceHeader);
                HeaderPart header = main.AddNewPart<HeaderPart>(headerId);
                header.Header = (Header)sourceHeader.Header.CloneNode(true);
                foreach (ImagePart sourceImage in sourceHeader.ImageParts)
                {
                    string imageId = sourceHeader.GetIdOfPart(sourceImage);
                    ImagePart imagePart = header.AddImagePart(
                        ImagePartType.Jpeg,
                        imageId);
                    using (FileStream image = File.OpenRead(imagePath))
                        imagePart.FeedData(image);
                }
                header.Header.Save();
            }

            FooterPart sourceFooter = sourceMain.FooterParts.First();
            FooterPart footer = main.AddNewPart<FooterPart>("rId2");
            footer.Footer = (Footer)sourceFooter.Footer.CloneNode(true);

            main.Document.Save();
            styles.Styles.Save();
            settings.Settings.Save();
            footer.Footer.Save();

            target.PackageProperties.Title =
                "Documentación técnica Aprobado - Financiación educativa";
            target.PackageProperties.Creator =
                "Aprobado Soluciones Digitales S.A.S.";
            target.PackageProperties.Subject =
                "Arquitectura, operación, seguridad y hoja de ruta";
            target.PackageProperties.Keywords =
                "financiación educativa,Django,API,seguridad";
            target.PackageProperties.Created = DateTime.UtcNow;
            target.PackageProperties.Modified = DateTime.UtcNow;
        }
    }
}
'@

[AprobadoDocxRepacker]::Repack($source, $image, $temporary)
Copy-Item -LiteralPath $temporary -Destination $source -Force
Remove-Item -LiteralPath $temporary -Force
Write-Output "REPACKED=$source"
