from __future__ import annotations

import html
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
SOURCE = (
    ROOT
    / "docs"
    / "interno"
    / "arquitectura"
    / "documentacion_tecnica_fuente.html"
)
MEMBRETE = ROOT / "MembreteAprobado.jpg"
OUTPUT = (
    ROOT
    / "docs"
    / "interno"
    / "arquitectura"
    / "Documentacion_Tecnica_Aprobado_Financiacion_Educativa.docx"
)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def qn(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def sub(parent, prefix: str, name: str, attrs=None):
    attrs = attrs or {}
    return ET.SubElement(
        parent,
        qn(prefix, name),
        {qn(*key.split(":")) if ":" in key else key: str(value) for key, value in attrs.items()},
    )


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    def text(self) -> str:
        parts = []

        def collect(item):
            if isinstance(item, str):
                parts.append(item)
            else:
                for child in item.children:
                    collect(child)

        collect(self)
        return " ".join("".join(parts).split())


class TreeParser(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "link", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag.lower(), dict(attrs))
        self.stack[-1].children.append(node)
        if tag.lower() not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def paragraph(text: str = "", style: str | None = None, *, page_break=False, bullet=False):
    p = ET.Element(qn("w", "p"))
    ppr = sub(p, "w", "pPr")
    if style:
        sub(ppr, "w", "pStyle", {"w:val": style})
    if bullet:
        sub(ppr, "w", "ind", {"w:left": "360", "w:hanging": "180"})
    run = sub(p, "w", "r")
    if page_break:
        sub(run, "w", "br", {"w:type": "page"})
    else:
        node = sub(run, "w", "t")
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        node.text = f"• {text}" if bullet else text
    return p


def callout(text: str, warning=False):
    table = make_table([["", text]], header=False, first_width=180)
    cells = table.findall(f".//{qn('w', 'tc')}")
    first_shading = cells[0].find(f"{qn('w', 'tcPr')}/{qn('w', 'shd')}")
    first_shading.set(qn("w", "fill"), "FFD43B" if warning else "05A7D5")
    second_shading = cells[1].find(f"{qn('w', 'tcPr')}/{qn('w', 'shd')}")
    second_shading.set(qn("w", "fill"), "FFF9DF" if warning else "EDF9FC")
    return table


def make_table(rows: list[list[str]], *, header=True, first_width=None):
    table = ET.Element(qn("w", "tbl"))
    tblpr = sub(table, "w", "tblPr")
    sub(tblpr, "w", "tblW", {"w:w": "5000", "w:type": "pct"})
    borders = sub(tblpr, "w", "tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        sub(borders, "w", edge, {"w:val": "single", "w:sz": "4", "w:color": "B7C9D3"})
    sub(tblpr, "w", "tblLayout", {"w:type": "autofit"})
    grid = sub(table, "w", "tblGrid")
    column_count = max(len(row) for row in rows)
    for column in range(column_count):
        width = first_width if first_width and column == 0 else max(
            720,
            (10000 - (first_width or 0)) // max(1, column_count - (1 if first_width else 0)),
        )
        sub(grid, "w", "gridCol", {"w:w": str(width)})
    for row_index, values in enumerate(rows):
        tr = sub(table, "w", "tr")
        trpr = sub(tr, "w", "trPr")
        sub(trpr, "w", "cantSplit", {"w:val": "on"})
        for col_index, value in enumerate(values):
            tc = sub(tr, "w", "tc")
            tcpr = sub(tc, "w", "tcPr")
            if first_width and col_index == 0:
                sub(tcpr, "w", "tcW", {"w:w": str(first_width), "w:type": "dxa"})
            fill = "0B4B73" if header and row_index == 0 else (
                "F2F7F9" if row_index % 2 == 0 else "FFFFFF"
            )
            sub(tcpr, "w", "shd", {"w:val": "clear", "w:fill": fill})
            p = sub(tc, "w", "p")
            ppr = sub(p, "w", "pPr")
            sub(ppr, "w", "spacing", {"w:after": "40"})
            run = sub(p, "w", "r")
            rpr = sub(run, "w", "rPr")
            sub(rpr, "w", "rFonts", {"w:ascii": "Aptos", "w:hAnsi": "Aptos"})
            if header and row_index == 0:
                sub(rpr, "w", "b", {"w:val": "on"})
                sub(rpr, "w", "color", {"w:val": "FFFFFF"})
            sub(rpr, "w", "sz", {"w:val": "17"})
            text = sub(run, "w", "t")
            text.text = value
    return table


def find_body(root: Node) -> Node:
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node.tag == "body":
            return node
        queue.extend(child for child in node.children if isinstance(child, Node))
    raise ValueError("HTML sin body")


def process_blocks(parent, nodes):
    for item in nodes:
        if isinstance(item, str) or item.tag in {"script", "style"}:
            continue
        classes = set(item.attrs.get("class", "").split())
        if item.tag == "div" and "cover" in classes:
            for child in item.children:
                if not isinstance(child, Node):
                    continue
                if child.tag == "h1":
                    parent.append(paragraph(child.text(), "Title"))
                elif child.tag == "h2":
                    parent.append(paragraph(child.text(), "Subtitle"))
                elif child.tag == "p":
                    parent.append(paragraph(child.text(), "Center"))
                elif child.tag == "div":
                    parent.append(callout(child.text()))
            parent.append(paragraph(page_break=True))
        elif item.tag == "div" and "toc" in classes:
            parent.append(paragraph("Tabla de contenido", "Heading1"))
            p = paragraph("", "Normal")
            field = sub(p, "w", "fldSimple", {"w:instr": 'TOC \\o "1-3" \\h \\z \\u'})
            run = sub(field, "w", "r")
            text = sub(run, "w", "t")
            text.text = "Actualiza este campo en Word para recalcular la tabla."
            parent.append(p)
            parent.append(paragraph(page_break=True))
        elif item.tag == "div" and "callout" in classes:
            parent.append(callout(item.text(), "warning" in classes))
        elif item.tag in {"h1", "h2", "h3"}:
            parent.append(paragraph(item.text(), f"Heading{item.tag[-1]}"))
        elif item.tag == "p":
            parent.append(paragraph(item.text(), "Normal"))
        elif item.tag == "ul":
            for child in item.children:
                if isinstance(child, Node) and child.tag == "li":
                    parent.append(paragraph(child.text(), "Normal", bullet=True))
        elif item.tag == "table":
            rows = []
            for tr in (child for child in item.children if isinstance(child, Node) and child.tag == "tr"):
                cells = [
                    cell.text()
                    for cell in tr.children
                    if isinstance(cell, Node) and cell.tag in {"th", "td"}
                ]
                if cells:
                    rows.append(cells)
            if rows:
                parent.append(
                    make_table(
                        rows,
                        header=any(
                            isinstance(cell, Node) and cell.tag == "th"
                            for cell in next(
                                child
                                for child in item.children
                                if isinstance(child, Node) and child.tag == "tr"
                            ).children
                        ),
                        first_width=420 if "flow" in classes else None,
                    )
                )
                parent.append(paragraph(""))
        else:
            process_blocks(parent, item.children)


def styles_xml():
    styles = ET.Element(qn("w", "styles"))
    defaults = sub(styles, "w", "docDefaults")
    rdefault = sub(sub(defaults, "w", "rPrDefault"), "w", "rPr")
    sub(rdefault, "w", "rFonts", {"w:ascii": "Aptos", "w:hAnsi": "Aptos"})
    sub(rdefault, "w", "sz", {"w:val": "19"})
    pdefault = sub(sub(defaults, "w", "pPrDefault"), "w", "pPr")
    sub(pdefault, "w", "spacing", {"w:after": "100", "w:line": "260", "w:lineRule": "auto"})

    def style(style_id, name, *, size, color, bold=False, outline=None, before=0, after=100, center=False):
        node = sub(styles, "w", "style", {"w:type": "paragraph", "w:styleId": style_id})
        sub(node, "w", "name", {"w:val": name})
        if style_id != "Normal":
            sub(node, "w", "basedOn", {"w:val": "Normal"})
        sub(node, "w", "qFormat")
        ppr = sub(node, "w", "pPr")
        if outline is not None:
            sub(ppr, "w", "keepNext", {"w:val": "on"})
        sub(ppr, "w", "spacing", {"w:before": str(before), "w:after": str(after)})
        if outline is not None:
            sub(ppr, "w", "outlineLvl", {"w:val": str(outline)})
        if center:
            sub(ppr, "w", "jc", {"w:val": "center"})
        rpr = sub(node, "w", "rPr")
        sub(rpr, "w", "rFonts", {"w:ascii": "Aptos Display", "w:hAnsi": "Aptos Display"})
        if bold:
            sub(rpr, "w", "b", {"w:val": "on"})
        sub(rpr, "w", "color", {"w:val": color})
        sub(rpr, "w", "sz", {"w:val": str(size)})

    style("Normal", "Normal", size=19, color="123047")
    style("Title", "Title", size=54, color="0B4B73", bold=True, center=True, after=240)
    style("Subtitle", "Subtitle", size=32, color="0B4B73", bold=True, center=True, after=180)
    style("Center", "Center", size=19, color="123047", center=True)
    style("Heading1", "heading 1", size=34, color="0B4B73", bold=True, outline=0, before=160, after=160)
    style("Heading2", "heading 2", size=25, color="05A7D5", bold=True, outline=1, before=140, after=90)
    style("Heading3", "heading 3", size=21, color="0B4B73", bold=True, outline=2, before=100, after=70)
    return ET.tostring(styles, encoding="utf-8", xml_declaration=True)


def build_document():
    parser = TreeParser()
    parser.feed(SOURCE.read_text(encoding="utf-8"))
    document = ET.Element(qn("w", "document"))
    body = sub(document, "w", "body")
    process_blocks(body, find_body(parser.root).children)
    sect = sub(body, "w", "sectPr")
    sub(sect, "w", "headerReference", {"w:type": "default", "r:id": "rId1"})
    sub(sect, "w", "headerReference", {"w:type": "first", "r:id": "rId5"})
    sub(sect, "w", "footerReference", {"w:type": "default", "r:id": "rId2"})
    sub(sect, "w", "pgSz", {"w:w": "12240", "w:h": "15840"})
    sub(
        sect,
        "w",
        "pgMar",
        {
            "w:top": "3160",
            "w:right": "1160",
            "w:bottom": "2840",
            "w:left": "1160",
            "w:header": "0",
            "w:footer": "320",
            "w:gutter": "0",
        },
    )
    sub(sect, "w", "titlePg")
    return ET.tostring(document, encoding="utf-8", xml_declaration=True)


def header_text_xml():
    hdr = ET.Element(qn("w", "hdr"))
    p = sub(hdr, "w", "p")
    ppr = sub(p, "w", "pPr")
    sub(ppr, "w", "jc", {"w:val": "right"})
    run = sub(p, "w", "r")
    rpr = sub(run, "w", "rPr")
    sub(rpr, "w", "b", {"w:val": "on"})
    sub(rpr, "w", "color", {"w:val": "0B4B73"})
    sub(rpr, "w", "sz", {"w:val": "18"})
    text = sub(run, "w", "t")
    text.text = "APROBADO  |  FINANCIACIÓN EDUCATIVA"
    return ET.tostring(hdr, encoding="utf-8", xml_declaration=True)


def header_image_xml():
    hdr = ET.Element(qn("w", "hdr"))
    p = sub(hdr, "w", "p")
    run = sub(p, "w", "r")
    drawing = sub(run, "w", "drawing")
    anchor = sub(
        drawing,
        "wp",
        "anchor",
        {
            "distT": "0",
            "distB": "0",
            "distL": "0",
            "distR": "0",
            "simplePos": "0",
            "relativeHeight": "0",
            "behindDoc": "1",
            "locked": "0",
            "layoutInCell": "1",
            "allowOverlap": "1",
        },
    )
    sub(anchor, "wp", "simplePos", {"x": "0", "y": "0"})
    pos_h = sub(anchor, "wp", "positionH", {"relativeFrom": "page"})
    sub(pos_h, "wp", "posOffset").text = "0"
    pos_v = sub(anchor, "wp", "positionV", {"relativeFrom": "page"})
    sub(pos_v, "wp", "posOffset").text = "0"
    sub(anchor, "wp", "extent", {"cx": "7772400", "cy": "10058400"})
    sub(
        anchor,
        "wp",
        "effectExtent",
        {"l": "0", "t": "0", "r": "0", "b": "0"},
    )
    sub(anchor, "wp", "wrapNone")
    sub(anchor, "wp", "docPr", {"id": "1", "name": "Membrete Aprobado"})
    sub(anchor, "wp", "cNvGraphicFramePr")
    graphic = sub(anchor, "a", "graphic")
    data = sub(
        graphic,
        "a",
        "graphicData",
        {"uri": "http://schemas.openxmlformats.org/drawingml/2006/picture"},
    )
    picture = sub(data, "pic", "pic")
    nv = sub(picture, "pic", "nvPicPr")
    sub(nv, "pic", "cNvPr", {"id": "0", "name": "MembreteAprobado.jpg"})
    sub(nv, "pic", "cNvPicPr")
    fill = sub(picture, "pic", "blipFill")
    sub(fill, "a", "blip", {"r:embed": "rId1"})
    stretch = sub(fill, "a", "stretch")
    sub(stretch, "a", "fillRect")
    sppr = sub(picture, "pic", "spPr")
    xfrm = sub(sppr, "a", "xfrm")
    sub(xfrm, "a", "off", {"x": "0", "y": "0"})
    sub(xfrm, "a", "ext", {"cx": "7772400", "cy": "10058400"})
    geom = sub(sppr, "a", "prstGeom", {"prst": "rect"})
    sub(geom, "a", "avLst")
    return ET.tostring(hdr, encoding="utf-8", xml_declaration=True)


def footer_xml():
    ftr = ET.Element(qn("w", "ftr"))
    p = sub(ftr, "w", "p")
    ppr = sub(p, "w", "pPr")
    sub(ppr, "w", "jc", {"w:val": "right"})
    run = sub(p, "w", "r")
    rpr = sub(run, "w", "rPr")
    sub(rpr, "w", "b", {"w:val": "on"})
    sub(rpr, "w", "color", {"w:val": "0B4B73"})
    sub(rpr, "w", "sz", {"w:val": "16"})
    text = sub(run, "w", "t")
    text.text = "DOCUMENTACIÓN TÉCNICA  |  VERSIÓN 1.0  |  PÁGINA "
    field = sub(p, "w", "fldSimple", {"w:instr": "PAGE"})
    sub(field, "w", "r")
    return ET.tostring(ftr, encoding="utf-8", xml_declaration=True)


def write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main():
    if not SOURCE.exists() or not MEMBRETE.exists():
        raise SystemExit("Falta la fuente HTML o el membrete oficial.")
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        (base / "_rels").mkdir()
        (base / "docProps").mkdir()
        (base / "word" / "_rels").mkdir(parents=True)
        (base / "word" / "media").mkdir()

        write_text(
            base / "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="jpg" ContentType="image/jpeg"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        write_text(
            base / "_rels" / ".rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        write_text(
            base / "word" / "_rels" / "document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>
</Relationships>""",
        )
        write_text(
            base / "word" / "_rels" / "header2.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/MembreteAprobado.jpg"/>
</Relationships>""",
        )
        (base / "word" / "document.xml").write_bytes(build_document())
        (base / "word" / "styles.xml").write_bytes(styles_xml())
        (base / "word" / "header1.xml").write_bytes(header_text_xml())
        (base / "word" / "header2.xml").write_bytes(header_image_xml())
        (base / "word" / "footer1.xml").write_bytes(footer_xml())
        write_text(
            base / "word" / "settings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:updateFields w:val="true"/><w:compat/>
</w:settings>""",
        )
        now = datetime.now(timezone.utc).isoformat()
        write_text(
            base / "docProps" / "core.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="{NS['cp']}" xmlns:dc="{NS['dc']}" xmlns:dcterms="{NS['dcterms']}" xmlns:dcmitype="{NS['dcmitype']}" xmlns:xsi="{NS['xsi']}">
<dc:title>Documentación técnica Aprobado - Financiación educativa</dc:title>
<dc:creator>Aprobado Soluciones Digitales S.A.S.</dc:creator>
<dc:subject>Arquitectura, operación, seguridad y hoja de ruta</dc:subject>
<cp:keywords>financiación educativa,Django,API,seguridad</cp:keywords>
<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>""",
        )
        write_text(
            base / "docProps" / "app.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="{NS['vt']}">
<Application>Microsoft Office Word</Application><Company>Aprobado Soluciones Digitales S.A.S.</Company>
</Properties>""",
        )
        shutil.copy2(MEMBRETE, base / "word" / "media" / "MembreteAprobado.jpg")

        with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(base).as_posix())
    print(f"DOCX={OUTPUT}")
    print(f"SIZE={OUTPUT.stat().st_size}")


if __name__ == "__main__":
    main()
