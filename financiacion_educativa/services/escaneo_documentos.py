import re
import socket
import struct
from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone
from django.utils.module_loading import import_string

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoIntentoEscaneoDocumento,
    EstadoValidacionDocumento,
    OrigenIntentoEscaneoDocumento,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    IntentoEscaneoDocumento,
    ReaperturaEscaneoDocumento,
)


class VeredictoAntivirus(StrEnum):
    CLEAN = 'CLEAN'
    INFECTED = 'INFECTED'


class ErrorEscaneoDocumento(Exception):
    def __init__(self, codigo):
        self.codigo = codigo
        super().__init__(codigo)


@dataclass(frozen=True)
class ResultadoAntivirus:
    veredicto: VeredictoAntivirus
    proveedor: str
    firma_amenaza: str = ''


@dataclass(frozen=True)
class ResultadoProcesamientoEscaneo:
    documento_id: object
    estado: str
    intento_id: object = None
    procesado: bool = False
    codigo_error: str = ''


class ClamAVDocumentScanBackend:
    proveedor = 'clamav'
    tamano_bloque = 64 * 1024

    def __init__(self):
        self.unix_socket = settings.FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET
        self.host = settings.FINANCIACION_EDUCATIVA_CLAMAV_HOST
        self.port = settings.FINANCIACION_EDUCATIVA_CLAMAV_PORT
        self.connect_timeout = (
            settings.FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS
        )
        self.read_timeout = (
            settings.FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS
        )

    def _conectar(self):
        try:
            if self.unix_socket:
                conexion = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conexion.settimeout(self.connect_timeout)
                conexion.connect(self.unix_socket)
            else:
                conexion = socket.create_connection(
                    (self.host, self.port),
                    timeout=self.connect_timeout,
                )
            conexion.settimeout(self.read_timeout)
            return conexion
        except socket.timeout as exc:
            raise ErrorEscaneoDocumento('SCANNER_TIMEOUT') from exc
        except OSError as exc:
            raise ErrorEscaneoDocumento('SCANNER_UNAVAILABLE') from exc

    @staticmethod
    def _sanitizar_firma(valor):
        return re.sub(r'[^A-Za-z0-9._:\- ]+', '', valor).strip()[:120]

    def _interpretar_respuesta(self, respuesta):
        try:
            texto = respuesta.decode('utf-8', errors='strict').strip('\0\r\n ')
        except UnicodeDecodeError as exc:
            raise ErrorEscaneoDocumento('INVALID_RESPONSE') from exc
        if texto == 'stream: OK':
            return ResultadoAntivirus(VeredictoAntivirus.CLEAN, self.proveedor)
        coincidencia = re.fullmatch(r'stream: (.+) FOUND', texto)
        if coincidencia:
            firma = coincidencia.group(1)
            firma = self._sanitizar_firma(firma)
            if not firma:
                raise ErrorEscaneoDocumento('INVALID_RESPONSE')
            return ResultadoAntivirus(
                VeredictoAntivirus.INFECTED,
                self.proveedor,
                firma,
            )
        raise ErrorEscaneoDocumento('INVALID_RESPONSE')

    def escanear(self, archivo):
        conexion = self._conectar()
        try:
            conexion.sendall(b'zINSTREAM\0')
            while True:
                bloque = archivo.read(self.tamano_bloque)
                if not bloque:
                    break
                conexion.sendall(struct.pack('!I', len(bloque)))
                conexion.sendall(bloque)
            conexion.sendall(struct.pack('!I', 0))
            respuesta = b''
            while b'\0' not in respuesta and len(respuesta) <= 4096:
                bloque = conexion.recv(1024)
                if not bloque:
                    break
                respuesta += bloque
        except socket.timeout as exc:
            raise ErrorEscaneoDocumento('SCANNER_TIMEOUT') from exc
        except OSError as exc:
            raise ErrorEscaneoDocumento('SCANNER_UNAVAILABLE') from exc
        finally:
            conexion.close()
        if (
            not respuesta
            or len(respuesta) > 4096
            or b'\0' not in respuesta
            or respuesta.find(b'\0') != len(respuesta) - 1
        ):
            raise ErrorEscaneoDocumento('INVALID_RESPONSE')
        return self._interpretar_respuesta(respuesta)


def obtener_backend_escaneo():
    backend_class = import_string(
        settings.FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND
    )
    return backend_class()


def _validar_permiso_escaneo(actor, origen):
    if origen == OrigenIntentoEscaneoDocumento.COMMAND and actor is None:
        return
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.escanear_documento_financiacion'
        )
    ):
        raise PermissionDenied('No tiene permiso para solicitar escaneos.')


@transaction.atomic
def _iniciar_intento(*, documento_id, actor, origen):
    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=documento_id
    )
    if documento.estado_escaneo in {
        EstadoEscaneoDocumento.SAFE,
        EstadoEscaneoDocumento.BLOCKED,
    }:
        return documento, None, 'ALREADY_RESOLVED'
    if documento.estado_validacion == EstadoValidacionDocumento.APPROVED:
        return documento, None, 'ALREADY_REVIEWED'

    ahora = timezone.now()
    intento_activo = documento.intentos_escaneo.filter(
        estado=EstadoIntentoEscaneoDocumento.STARTED
    ).first()
    if intento_activo:
        antiguedad = (ahora - intento_activo.iniciado_en).total_seconds()
        if antiguedad < settings.FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS:
            return documento, intento_activo, 'IN_PROGRESS'
        IntentoEscaneoDocumento.objects.filter(pk=intento_activo.pk).update(
            estado=EstadoIntentoEscaneoDocumento.ERROR,
            codigo_error='STALE_ATTEMPT',
            finalizado_en=ahora,
        )

    numero = (
        documento.intentos_escaneo.order_by('-numero')
        .values_list('numero', flat=True)
        .first()
        or 0
    ) + 1
    presupuesto_adicional = documento.reaperturas_escaneo.aggregate(
        total=Sum('intentos_adicionales')
    )['total'] or 0
    limite_intentos = (
        settings.FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS
        + presupuesto_adicional
    )
    if numero > limite_intentos:
        return documento, None, 'MAX_ATTEMPTS'
    try:
        with transaction.atomic():
            intento = IntentoEscaneoDocumento.objects.create(
                documento=documento,
                numero=numero,
                origen=origen,
                solicitado_por=actor,
            )
    except IntegrityError:
        intento = documento.intentos_escaneo.filter(
            estado=EstadoIntentoEscaneoDocumento.STARTED
        ).first()
        return documento, intento, 'IN_PROGRESS'
    return documento, intento, ''


def _texto_controlado(valor, limite):
    return re.sub(r'[^A-Za-z0-9._:\- ]+', '', str(valor or '')).strip()[:limite]


@transaction.atomic
def _finalizar_intento(
    *,
    intento_id,
    resultado=None,
    codigo_error='',
    proveedor='',
):
    intento = IntentoEscaneoDocumento.objects.select_for_update().select_related(
        'documento'
    ).get(pk=intento_id)
    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=intento.documento_id
    )
    if intento.estado != EstadoIntentoEscaneoDocumento.STARTED:
        return documento

    ahora = timezone.now()
    valores_intento = {
        'firma_amenaza': '',
        'finalizado_en': ahora,
    }
    if codigo_error:
        valores_intento.update(
            estado=EstadoIntentoEscaneoDocumento.ERROR,
            codigo_error=codigo_error,
            proveedor=_texto_controlado(proveedor, 60),
            veredicto='',
        )
    elif resultado.veredicto == VeredictoAntivirus.CLEAN:
        valores_intento.update(
            estado=EstadoIntentoEscaneoDocumento.CLEAN,
            codigo_error='',
            veredicto=resultado.veredicto,
            proveedor=_texto_controlado(resultado.proveedor, 60),
        )
        documento.estado_escaneo = EstadoEscaneoDocumento.SAFE
    else:
        valores_intento.update(
            estado=EstadoIntentoEscaneoDocumento.INFECTED,
            codigo_error='',
            veredicto=resultado.veredicto,
            proveedor=_texto_controlado(resultado.proveedor, 60),
            firma_amenaza=_texto_controlado(resultado.firma_amenaza, 120),
        )
        documento.estado_escaneo = EstadoEscaneoDocumento.BLOCKED
    IntentoEscaneoDocumento.objects.filter(pk=intento.pk).update(
        **valores_intento
    )
    intento.refresh_from_db()
    if not codigo_error:
        documento.escaneado_en = ahora
        documento.ultimo_intento_limpio = (
            intento
            if intento.estado == EstadoIntentoEscaneoDocumento.CLEAN
            else None
        )
        documento.referencia_escaneo = f'{intento.proveedor}:{intento.pk}'[:120]
        documento.resultado_procesamiento = {
            'scan_attempt': str(intento.pk),
            'provider': intento.proveedor,
            'verdict': intento.veredicto,
        }
        documento.save(
            update_fields=[
                'estado_escaneo',
                'escaneado_en',
                'ultimo_intento_limpio',
                'referencia_escaneo',
                'resultado_procesamiento',
                'actualizado_en',
            ]
        )
    return documento


@transaction.atomic
def reabrir_escaneo_documento(*, documento, actor, motivo):
    _validar_permiso_escaneo(actor, OrigenIntentoEscaneoDocumento.ADMIN)
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError({'motivo': 'El motivo operativo es obligatorio.'})

    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=documento.pk
    )
    if documento.estado_escaneo != EstadoEscaneoDocumento.PENDING_SECURITY_SCAN:
        raise ValidationError('Solo puede reabrirse un documento pendiente.')
    if documento.intentos_escaneo.filter(
        estado=EstadoIntentoEscaneoDocumento.STARTED
    ).exists():
        raise ValidationError('No puede reabrirse mientras existe un intento activo.')

    numero_actual = documento.intentos_escaneo.aggregate(
        maximo=Max('numero')
    )['maximo'] or 0
    presupuesto_actual = documento.reaperturas_escaneo.aggregate(
        total=Sum('intentos_adicionales')
    )['total'] or 0
    limite_actual = (
        settings.FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS
        + presupuesto_actual
    )
    if numero_actual < limite_actual:
        raise ValidationError('El documento todavia dispone de intentos de escaneo.')
    if (
        documento.reaperturas_escaneo.count()
        >= settings.FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS
    ):
        raise ValidationError('El documento alcanzo el limite de reaperturas.')

    reapertura = ReaperturaEscaneoDocumento(
        documento=documento,
        autorizado_por=actor,
        motivo=motivo,
        intentos_adicionales=(
            settings.FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS
        ),
    )
    reapertura.full_clean()
    reapertura.save()
    return reapertura


def procesar_escaneo_documento(
    *,
    documento,
    actor=None,
    origen=OrigenIntentoEscaneoDocumento.ADMIN,
    backend=None,
):
    _validar_permiso_escaneo(actor, origen)
    documento, intento, omision = _iniciar_intento(
        documento_id=documento.pk,
        actor=actor,
        origen=origen,
    )
    if omision:
        return ResultadoProcesamientoEscaneo(
            documento_id=documento.pk,
            estado=omision,
            intento_id=getattr(intento, 'pk', None),
        )

    scanner = backend
    try:
        scanner = scanner or obtener_backend_escaneo()
        if not documento.archivo:
            raise ErrorEscaneoDocumento('READ_ERROR')
        with documento.archivo.open('rb') as archivo:
            resultado = scanner.escanear(archivo)
        if not isinstance(resultado, ResultadoAntivirus):
            raise ErrorEscaneoDocumento('INVALID_RESPONSE')
        if not isinstance(resultado.veredicto, VeredictoAntivirus):
            raise ErrorEscaneoDocumento('INVALID_RESPONSE')
    except ErrorEscaneoDocumento as error:
        _finalizar_intento(
            intento_id=intento.pk,
            codigo_error=error.codigo,
            proveedor=getattr(scanner, 'proveedor', ''),
        )
        return ResultadoProcesamientoEscaneo(
            documento_id=documento.pk,
            estado='ERROR',
            intento_id=intento.pk,
            procesado=True,
            codigo_error=error.codigo,
        )
    except (OSError, ValueError):
        _finalizar_intento(
            intento_id=intento.pk,
            codigo_error='READ_ERROR',
            proveedor=getattr(scanner, 'proveedor', ''),
        )
        return ResultadoProcesamientoEscaneo(
            documento_id=documento.pk,
            estado='ERROR',
            intento_id=intento.pk,
            procesado=True,
            codigo_error='READ_ERROR',
        )
    except Exception:
        _finalizar_intento(
            intento_id=intento.pk,
            codigo_error='INTERNAL_ERROR',
            proveedor=getattr(scanner, 'proveedor', ''),
        )
        return ResultadoProcesamientoEscaneo(
            documento_id=documento.pk,
            estado='ERROR',
            intento_id=intento.pk,
            procesado=True,
            codigo_error='INTERNAL_ERROR',
        )

    documento = _finalizar_intento(intento_id=intento.pk, resultado=resultado)
    return ResultadoProcesamientoEscaneo(
        documento_id=documento.pk,
        estado=documento.estado_escaneo,
        intento_id=intento.pk,
        procesado=True,
    )
