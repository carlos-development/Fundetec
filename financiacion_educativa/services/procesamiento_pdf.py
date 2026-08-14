import re
import time
import unicodedata
import multiprocessing
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings


TOKENS_CONTENIDO_ACTIVO = (
    b'/JavaScript',
    b'/JS',
    b'/OpenAction',
    b'/AA',
    b'/Launch',
    b'/RichMedia',
    b'/SubmitForm',
    b'/EmbeddedFiles',
    b'/FileAttachment',
)


class ErrorProcesamientoPDF(Exception):
    def __init__(self, codigo, *, corregible=False, temporal=False, metadata=None):
        self.codigo = codigo
        self.corregible = corregible
        self.temporal = temporal
        self.metadata = dict(metadata or {})
        super().__init__(codigo)


@dataclass(frozen=True)
class PaginaExtraida:
    numero: int
    texto: str
    imagen_png: bytes | None = None


@dataclass(frozen=True)
class ResultadoProcesamientoPDF:
    numero_paginas: int
    pdf_cifrado: bool
    contenido_activo_detectado: bool
    metodo_extraccion: str
    paginas: tuple[PaginaExtraida, ...]

    @property
    def paginas_analizadas(self):
        return tuple(pagina.numero for pagina in self.paginas)


def _comprobar_timeout(iniciado_en):
    if (
        time.monotonic() - iniciado_en
        > settings.FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS
    ):
        raise ErrorProcesamientoPDF('PDF_PROCESSING_TIMEOUT', temporal=True)


def _normalizar_texto(texto):
    texto = unicodedata.normalize('NFKC', texto or '')
    texto = ''.join(
        caracter
        for caracter in texto
        if caracter in '\n\t' or unicodedata.category(caracter)[0] != 'C'
    )
    texto = re.sub(r'[ \t]+', ' ', texto)
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    return texto.strip()


def _contar_objetos(reader):
    return sum(
        len(objetos)
        for objetos in getattr(reader, 'xref', {}).values()
        if isinstance(objetos, dict)
    )


def _validar_estructura(contenido, *, iniciado_en):
    if not contenido.startswith(b'%PDF-'):
        raise ErrorProcesamientoPDF('PDF_INVALID_SIGNATURE', corregible=True)
    if len(contenido) > settings.FINANCIACION_EDUCATIVA_PDF_MAX_BYTES:
        raise ErrorProcesamientoPDF('PDF_TOO_LARGE', corregible=True)
    if b'/EmbeddedFiles' in contenido or b'/FileAttachment' in contenido:
        raise ErrorProcesamientoPDF(
            'PDF_EMBEDDED_FILE',
            corregible=True,
            metadata={'contenido_activo_detectado': True},
        )
    contenido_activo = any(token in contenido for token in TOKENS_CONTENIDO_ACTIVO)
    if contenido_activo:
        raise ErrorProcesamientoPDF(
            'PDF_ACTIVE_CONTENT',
            corregible=True,
            metadata={'contenido_activo_detectado': True},
        )
    longitudes = [
        int(valor)
        for valor in re.findall(rb'/Length\s+(\d+)', contenido)
    ]
    if longitudes and max(longitudes) > (
        settings.FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES
    ):
        raise ErrorProcesamientoPDF('PDF_OBJECT_TOO_LARGE', corregible=True)
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(contenido), strict=True)
    except Exception as error:
        raise ErrorProcesamientoPDF('PDF_CORRUPT', corregible=True) from error
    _comprobar_timeout(iniciado_en)
    if reader.is_encrypted:
        raise ErrorProcesamientoPDF(
            'PDF_ENCRYPTED',
            corregible=True,
            metadata={'pdf_cifrado': True},
        )
    try:
        numero_paginas = len(reader.pages)
    except Exception as error:
        raise ErrorProcesamientoPDF('PDF_CORRUPT', corregible=True) from error
    if numero_paginas < 1:
        raise ErrorProcesamientoPDF('PDF_NO_PAGES', corregible=True)
    if numero_paginas > settings.FINANCIACION_EDUCATIVA_PDF_MAX_PAGES:
        raise ErrorProcesamientoPDF('PDF_TOO_MANY_PAGES', corregible=True)
    if _contar_objetos(reader) > settings.FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS:
        raise ErrorProcesamientoPDF('PDF_TOO_MANY_OBJECTS', corregible=True)
    try:
        if getattr(reader, 'attachments', None):
            raise ErrorProcesamientoPDF('PDF_EMBEDDED_FILE', corregible=True)
    except ErrorProcesamientoPDF:
        raise
    except Exception as error:
        raise ErrorProcesamientoPDF('PDF_CORRUPT', corregible=True) from error
    return reader, numero_paginas


def _extraer_texto(reader, *, iniciado_en):
    maximo = settings.FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS
    paginas = []
    total = 0
    for indice, pagina in enumerate(reader.pages, start=1):
        _comprobar_timeout(iniciado_en)
        try:
            texto = _normalizar_texto(pagina.extract_text() or '')
        except Exception as error:
            raise ErrorProcesamientoPDF('PDF_TEXT_EXTRACTION_ERROR', temporal=True) from error
        restante = maximo - total
        if restante <= 0:
            raise ErrorProcesamientoPDF('PDF_TEXT_LIMIT_EXCEEDED', corregible=True)
        if len(texto) > restante:
            raise ErrorProcesamientoPDF('PDF_TEXT_LIMIT_EXCEEDED', corregible=True)
        paginas.append(PaginaExtraida(numero=indice, texto=texto))
        total += len(texto)
    return paginas


def _seleccionar_paginas_para_render(paginas):
    maximo = settings.FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES
    insuficientes = [p.numero for p in paginas if len(p.texto) < 80]
    candidatas = []
    for numero in (1, *insuficientes):
        if numero not in candidatas:
            candidatas.append(numero)
    return tuple(candidatas[:maximo])


def _renderizar(contenido, numeros, *, iniciado_en):
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise ErrorProcesamientoPDF('PDF_RENDERER_UNAVAILABLE', temporal=True) from error
    try:
        documento = pdfium.PdfDocument(contenido)
    except Exception as error:
        raise ErrorProcesamientoPDF('PDF_RENDER_ERROR', temporal=True) from error
    imagenes = {}
    try:
        for numero in numeros:
            _comprobar_timeout(iniciado_en)
            pagina = documento[numero - 1]
            ancho, alto = pagina.get_size()
            escala_maxima = min(
                2.0,
                (
                    settings.FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE
                    / max(ancho * alto, 1)
                ) ** 0.5 * 0.99,
            )
            if escala_maxima <= 0:
                raise ErrorProcesamientoPDF('PDF_PIXEL_LIMIT_EXCEEDED', corregible=True)
            bitmap = pagina.render(scale=escala_maxima, rev_byteorder=True)
            imagen = bitmap.to_pil().convert('RGB')
            if imagen.width * imagen.height > (
                settings.FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE
            ):
                raise ErrorProcesamientoPDF('PDF_PIXEL_LIMIT_EXCEEDED', corregible=True)
            salida = BytesIO()
            imagen.save(salida, format='PNG', optimize=True)
            imagenes[numero] = salida.getvalue()
            salida.close()
            imagen.close()
            bitmap.close()
            pagina.close()
    except ErrorProcesamientoPDF:
        raise
    except Exception as error:
        raise ErrorProcesamientoPDF('PDF_RENDER_ERROR', temporal=True) from error
    finally:
        documento.close()
    return imagenes


def _procesar_pdf_directo(contenido):
    """Valida, extrae y renderiza en memoria; nunca ejecuta contenido del PDF."""
    iniciado_en = time.monotonic()
    reader, numero_paginas = _validar_estructura(
        contenido,
        iniciado_en=iniciado_en,
    )
    paginas = _extraer_texto(reader, iniciado_en=iniciado_en)
    numeros_render = _seleccionar_paginas_para_render(paginas)
    imagenes = _renderizar(contenido, numeros_render, iniciado_en=iniciado_en)
    combinadas = tuple(
        PaginaExtraida(
            numero=pagina.numero,
            texto=pagina.texto,
            imagen_png=imagenes.get(pagina.numero),
        )
        for pagina in paginas
        if pagina.texto or pagina.numero in imagenes
    )
    tiene_texto = any(pagina.texto for pagina in combinadas)
    tiene_imagen = any(pagina.imagen_png for pagina in combinadas)
    if tiene_texto and tiene_imagen:
        metodo = 'PDF_HYBRID'
    elif tiene_texto:
        metodo = 'PDF_TEXT'
    else:
        metodo = 'PDF_RENDER'
    return ResultadoProcesamientoPDF(
        numero_paginas=numero_paginas,
        pdf_cifrado=False,
        contenido_activo_detectado=False,
        metodo_extraccion=metodo,
        paginas=combinadas,
    )


def _limitar_recursos_subproceso():
    try:
        import resource

        bytes_memoria = (
            settings.FINANCIACION_EDUCATIVA_PDF_MAX_MEMORY_MB * 1024 * 1024
        )
        resource.setrlimit(resource.RLIMIT_AS, (bytes_memoria, bytes_memoria))
        segundos_cpu = max(
            1,
            int(settings.FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS),
        )
        resource.setrlimit(resource.RLIMIT_CPU, (segundos_cpu, segundos_cpu + 1))
    except (ImportError, AttributeError, OSError, ValueError):
        # Windows no ofrece resource; el proceso sigue limitado por tiempo y entrada.
        return


def _worker_pdf(contenido, conexion):
    _limitar_recursos_subproceso()
    try:
        conexion.send(('OK', _procesar_pdf_directo(contenido)))
    except ErrorProcesamientoPDF as error:
        conexion.send((
            'CONTROLLED_ERROR',
            error.codigo,
            error.corregible,
            error.temporal,
            error.metadata,
        ))
    except Exception:
        conexion.send(('INTERNAL_ERROR',))
    finally:
        conexion.close()


def procesar_pdf_seguro(contenido):
    """Procesa PDF en un subproceso descartable con timeout de pared."""
    if not settings.FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS:
        return _procesar_pdf_directo(contenido)
    contexto = multiprocessing.get_context('spawn')
    receptor, emisor = contexto.Pipe(duplex=False)
    proceso = contexto.Process(target=_worker_pdf, args=(contenido, emisor))
    proceso.daemon = True
    proceso.start()
    emisor.close()
    timeout = settings.FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS
    try:
        if not receptor.poll(timeout):
            proceso.terminate()
            proceso.join(timeout=2)
            if proceso.is_alive():
                proceso.kill()
                proceso.join(timeout=2)
            raise ErrorProcesamientoPDF('PDF_PROCESSING_TIMEOUT', temporal=True)
        mensaje = receptor.recv()
    except EOFError as error:
        raise ErrorProcesamientoPDF('PDF_PROCESSING_ERROR', temporal=True) from error
    finally:
        receptor.close()
        proceso.join(timeout=2)
        if proceso.is_alive():
            proceso.terminate()
            proceso.join(timeout=2)
    if mensaje[0] == 'OK':
        return mensaje[1]
    if mensaje[0] == 'CONTROLLED_ERROR':
        raise ErrorProcesamientoPDF(
            mensaje[1],
            corregible=mensaje[2],
            temporal=mensaje[3],
            metadata=mensaje[4],
        )
    raise ErrorProcesamientoPDF('PDF_PROCESSING_ERROR', temporal=True)
