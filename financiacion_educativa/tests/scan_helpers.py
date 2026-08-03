from django.contrib.auth.models import Permission
from django.db import connection

from financiacion_educativa.choices import EstadoEscaneoDocumento
from financiacion_educativa.models import DocumentoFinanciacion
from financiacion_educativa.services.escaneo_documentos import (
    procesar_escaneo_documento,
)
from financiacion_educativa.tests.scan_backends import (
    BackendInfectado,
    BackendLimpio,
)


def conceder_permisos_documentales(usuario):
    permisos = Permission.objects.filter(
        content_type__app_label='financiacion_educativa',
        codename__in={
            'escanear_documento_financiacion',
            'revisar_documento_financiacion',
        },
    )
    usuario.user_permissions.add(*permisos)
    for atributo in ('_perm_cache', '_user_perm_cache'):
        usuario.__dict__.pop(atributo, None)


def forzar_seguridad_documento_historico(
    *,
    documento,
    estado_escaneo,
    ultimo_intento_limpio_id=None,
    escaneo_requerido_desde=None,
):
    """Prepara por SQL un estado anterior a la proteccion del QuerySet."""
    pk = DocumentoFinanciacion._meta.pk.get_db_prep_value(
        documento.pk,
        connection,
    )
    campo_intento = DocumentoFinanciacion._meta.get_field(
        'ultimo_intento_limpio'
    )
    intento = campo_intento.target_field.get_db_prep_value(
        ultimo_intento_limpio_id,
        connection,
    )
    campo_marcador = DocumentoFinanciacion._meta.get_field(
        'escaneo_requerido_desde'
    )
    marcador = campo_marcador.get_db_prep_value(
        escaneo_requerido_desde,
        connection,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            'UPDATE financiacion_educativa_documentofinanciacion '
            'SET estado_escaneo = %s, ultimo_intento_limpio_id = %s, '
            'escaneo_requerido_desde = %s WHERE id = %s',
            [estado_escaneo, intento, marcador, pk],
        )


def registrar_resultado_escaneo(
    *,
    documento,
    actor,
    estado,
    referencia_escaneo='',
):
    del referencia_escaneo
    backend = {
        EstadoEscaneoDocumento.SAFE: BackendLimpio,
        EstadoEscaneoDocumento.BLOCKED: BackendInfectado,
    }[estado]()
    resultado = procesar_escaneo_documento(
        documento=documento,
        actor=actor,
        backend=backend,
    )
    documento.refresh_from_db()
    if documento.estado_escaneo != estado:
        raise AssertionError(
            f'El doble produjo {resultado.estado}, se esperaba {estado}.'
        )
    return documento
