import hashlib

from django.core.exceptions import ValidationError

from financiacion_educativa.models import DocumentoFinanciacion


def calcular_sha256_archivo(archivo):
    if not archivo:
        raise ValidationError({'archivo': 'El archivo es obligatorio.'})

    posicion = archivo.tell() if hasattr(archivo, 'tell') else None
    if hasattr(archivo, 'seek'):
        archivo.seek(0)
    digest = hashlib.sha256()
    for bloque in iter(lambda: archivo.read(64 * 1024), b''):
        digest.update(bloque)
    if hasattr(archivo, 'seek'):
        archivo.seek(posicion or 0)
    return digest.hexdigest()


def registrar_documento(
    *,
    solicitud,
    tipo,
    origen_captura,
    participante=None,
    archivo=None,
    referencia_almacenamiento='',
    nombre_original='',
    content_type='',
    tamano_bytes=None,
):
    sha256 = calcular_sha256_archivo(archivo) if archivo else ''
    documento = DocumentoFinanciacion(
        solicitud=solicitud,
        participante=participante,
        tipo=tipo,
        archivo=archivo,
        referencia_almacenamiento=referencia_almacenamiento,
        nombre_original=nombre_original or getattr(archivo, 'name', ''),
        content_type=content_type or getattr(archivo, 'content_type', ''),
        tamano_bytes=tamano_bytes if tamano_bytes is not None else getattr(archivo, 'size', None),
        origen_captura=origen_captura,
        sha256=sha256,
        resultado_procesamiento={},
        nivel_confianza=None,
    )
    documento.full_clean()
    documento.save()
    return documento
