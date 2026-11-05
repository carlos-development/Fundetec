import hashlib
import json
import re
import uuid
import warnings
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone
from PIL import Image
from pypdf import PdfReader

from financiacion_educativa.models_recaudos import (
    ArchivoRecaudoEducativo, IntentoEscaneoRecaudo, CuentaRecaudoEducativa,
    ReceptorBrebEducativo, EventoReceptorBrebEducativo, ReporteRecaudoEducativo,
    ConciliacionRecaudoEducativo, RechazoReporteRecaudoEducativo,
    normalizar_identificador_cuenta,
)
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from .autorizacion import usuario_es_propietario_solicitud
from .escaneo_documentos import (
    ErrorEscaneoDocumento, ResultadoAntivirus, VeredictoAntivirus, obtener_backend_escaneo,
)

PERMISO_CONFIGURAR = 'financiacion_educativa.configurar_receptor_breb'
PERMISO_CONSULTAR = 'financiacion_educativa.consultar_recaudos_educativos'
PERMISO_CONCILIAR = 'financiacion_educativa.conciliar_recaudos_educativos'
PERMISO_ESCANEAR = 'financiacion_educativa.escanear_comprobantes_educativos'
OBSERVACION_SIN_ADICIONAL = 'Sin observacion adicional registrada.'


def evidencias_bancarias_elegibles(actor):
    if not actor or not actor.is_active or not actor.has_perm(PERMISO_CONCILIAR):
        return ArchivoRecaudoEducativo.objects.none()
    ultimo = IntentoEscaneoRecaudo.objects.filter(archivo_id=OuterRef('pk')).order_by('-numero')
    return ArchivoRecaudoEducativo.objects.filter(
        creado_por=actor, finalidad='BANK_EVIDENCE',
    ).annotate(
        ultimo_estado=Subquery(ultimo.values('estado')[:1]),
        ultimo_hash=Subquery(ultimo.values('sha256')[:1]),
    ).filter(ultimo_estado='CLEAN', ultimo_hash=F('sha256')).order_by('-creado_en', 'pk')


def _verificar_integridad_soporte(archivo, campo=None):
    try:
        with archivo.archivo.open('rb') as stream:
            contenido = stream.read(settings.FINANCIACION_EDUCATIVA_RECAUDO_MAX_BYTES + 1)
    except (OSError, ValueError):
        mensaje = 'El soporte no esta disponible. Recargue y consulte su estado.'
        raise ValidationError({campo: mensaje} if campo else mensaje) from None
    if len(contenido) != archivo.tamano_bytes or hashlib.sha256(contenido).hexdigest() != archivo.sha256:
        mensaje = 'La integridad del soporte no coincide con el escaneo. No se puede confirmar.'
        raise ValidationError({campo: mensaje} if campo else mensaje)
    return contenido


def leer_soporte_seguro(archivo):
    if archivo.estado_escaneo != 'SAFE':
        raise ValidationError('El soporte debe superar el escaneo antes de consultarlo.')
    return _verificar_integridad_soporte(archivo)


def habilitados():
    if not settings.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED:
        raise ValidationError('El canal de recaudos todavia no esta habilitado.')


def exigir_permiso(actor, permiso):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.has_perm(permiso):
        raise PermissionDenied('No tiene permiso para realizar esta operacion.')


def monto_cop(valor):
    try:
        monto = Decimal(str(valor))
        if not monto.is_finite() or monto <= 0 or monto >= Decimal('100000000000000'):
            raise ValueError
        if monto.as_tuple().exponent < -2:
            raise ValueError
        return monto.quantize(Decimal('.01'))
    except (ValueError, InvalidOperation):
        raise ValidationError('Ingrese un monto COP positivo con hasta dos decimales.') from None


def fecha_transferencia(valor):
    if not isinstance(valor, date) or valor > timezone.localdate():
        raise ValidationError('La fecha de transferencia no puede estar en el futuro.')
    return valor


def validar_archivo(archivo):
    limite = settings.FINANCIACION_EDUCATIVA_RECAUDO_MAX_BYTES
    if not archivo or not 0 < archivo.size <= limite:
        raise ValidationError('Adjunte un JPG, PNG o PDF dentro del limite permitido.')
    archivo.seek(0)
    contenido = archivo.read(limite + 1)
    archivo.seek(0)
    if len(contenido) != archivo.size or len(contenido) > limite:
        raise ValidationError('El archivo no pudo validarse completamente.')
    try:
        if contenido.startswith(b'%PDF-'):
            pdf = PdfReader(BytesIO(contenido), strict=True)
            if pdf.is_encrypted or not 1 <= len(pdf.pages) <= 20:
                raise ValueError
            # Fuerza el parseo de cada pagina, sin extraer ni guardar su contenido.
            for pagina in pdf.pages:
                _ = pagina.mediabox
            mime = 'application/pdf'
            extensiones = ('.pdf',)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(contenido)) as imagen:
                    if imagen.format not in ('JPEG', 'PNG') or imagen.width * imagen.height > 20000000:
                        raise ValueError
                    mime = 'image/jpeg' if imagen.format == 'JPEG' else 'image/png'
                    imagen.verify()
            extensiones = ('.jpg', '.jpeg') if mime == 'image/jpeg' else ('.png',)
        if not archivo.name.lower().endswith(extensiones):
            raise ValueError
    except Exception:
        raise ValidationError('El formato real debe ser JPG, PNG o PDF valido y no cifrado.') from None
    return contenido, mime, hashlib.sha256(contenido).hexdigest()


def _guardar_archivo(datos, actor, finalidad):
    contenido, mime, sha256 = datos
    registro = ArchivoRecaudoEducativo(
        content_type=mime, sha256=sha256, tamano_bytes=len(contenido), creado_por=actor, finalidad=finalidad,
    )
    registro.archivo.save('evidencia', ContentFile(contenido), save=False)
    try:
        registro.save()
    except Exception:
        registro.archivo.delete(save=False)
        raise
    return registro


def registrar_evidencia_bancaria(*, archivo, actor):
    exigir_permiso(actor, PERMISO_CONCILIAR)
    habilitados()
    return _guardar_archivo(validar_archivo(archivo), actor, 'BANK_EVIDENCE')


def crear_receptor(*, entidad, identificador_cuenta, titular, llave, qr, actor):
    exigir_permiso(actor, PERMISO_CONFIGURAR)
    datos = validar_archivo(qr)
    guardado = None
    try:
        with transaction.atomic():
            cuenta, _ = CuentaRecaudoEducativa.objects.get_or_create(
                entidad=normalizar_identificador_cuenta(entidad), identificador_cuenta=normalizar_identificador_cuenta(identificador_cuenta),
                defaults={'creada_por': actor},
            )
            guardado = _guardar_archivo(datos, actor, 'QR')
            return ReceptorBrebEducativo.objects.create(
                cuenta=cuenta, titular=titular.strip(), llave=llave.strip(), qr=guardado, creado_por=actor,
            )
    except Exception:
        if guardado:
            guardado.archivo.delete(save=False)
        raise


def receptor_vigente():
    evento = EventoReceptorBrebEducativo.objects.select_related('receptor__cuenta', 'receptor__qr').order_by('-pk').first()
    if evento and evento.habilitado and evento.receptor.qr.estado_escaneo == 'SAFE':
        try:
            _verificar_integridad_soporte(evento.receptor.qr)
        except ValidationError:
            return None
        return evento.receptor
    return None


@transaction.atomic
def activar_receptor(*, receptor_id, actor, motivo, habilitado=True):
    exigir_permiso(actor, PERMISO_CONFIGURAR)
    if not motivo.strip():
        raise ValidationError('Registre el motivo del cambio de receptor.')
    receptor = ReceptorBrebEducativo.objects.select_for_update().get(pk=receptor_id)
    if habilitado and receptor.qr.estado_escaneo != 'SAFE':
        raise ValidationError('El QR debe tener un escaneo limpio antes de activarlo.')
    if habilitado:
        leer_soporte_seguro(receptor.qr)
    return EventoReceptorBrebEducativo.objects.create(
        receptor=receptor, habilitado=habilitado, actor=actor, motivo=motivo.strip(),
    )


def obligacion_del_usuario(*, solicitud_id, actor):
    fotografia = obligaciones_autorizadas().filter(solicitud_id=solicitud_id).first()
    if not fotografia or not actor.is_active or not usuario_es_propietario_solicitud(actor, fotografia.solicitud):
        raise PermissionDenied('No tiene acceso a esta obligacion.')
    return fotografia


def reportar_pago(*, solicitud_id, actor, receptor_id, clave, proposito, monto, fecha, referencia, archivo):
    habilitados()
    obligacion_del_usuario(solicitud_id=solicitud_id, actor=actor)
    monto = monto_cop(monto)
    fecha = fecha_transferencia(fecha)
    try:
        clave = uuid.UUID(str(clave))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('La clave de reporte no es valida.') from None
    if proposito not in ReporteRecaudoEducativo.Proposito.values:
        raise ValidationError('El proposito del reporte no es valido.')
    referencia = (referencia or '').strip()
    try:
        datos = validar_archivo(archivo)
    except ValidationError as error:
        raise ValidationError({'archivo': error.messages}) from None
    payload = hashlib.sha256(json.dumps({
        'receptor': str(receptor_id), 'proposito': proposito, 'monto': str(monto),
        'fecha': fecha.isoformat(), 'referencia': referencia, 'sha256': datos[2],
    }, sort_keys=True).encode()).hexdigest()
    guardado = None
    try:
        with transaction.atomic():
            SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=solicitud_id)
            fotografia = obligacion_del_usuario(solicitud_id=solicitud_id, actor=actor)
            anterior = ReporteRecaudoEducativo.objects.filter(
                solicitud_id=solicitud_id, reportado_por=actor, clave_idempotencia=clave,
            ).first()
            if anterior:
                if anterior.payload_sha256 != payload:
                    raise ValidationError('Esta clave ya fue utilizada con datos diferentes.')
                return anterior, True
            receptor = receptor_vigente()
            if not receptor or str(receptor.pk) != str(receptor_id):
                raise ValidationError({'receptor_id': 'El medio de pago cambio o no esta disponible. Consulte nuevamente.'})
            guardado = _guardar_archivo(datos, actor, 'RECEIPT')
            reporte = ReporteRecaudoEducativo.objects.create(
                solicitud_id=solicitud_id, fotografia=fotografia, receptor=receptor,
                comprobante=guardado, reportado_por=actor, clave_idempotencia=clave,
                payload_sha256=payload, proposito=proposito, monto_reportado=monto,
                fecha_declarada=fecha, referencia_declarada=referencia,
                cuota_sugerida=fotografia.proxima_cuota,
            )
            return reporte, False
    except Exception:
        if guardado:
            guardado.archivo.delete(save=False)
        raise


def referencia_bancaria(valor):
    referencia = valor.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9._/-]{0,119}', referencia):
        raise ValidationError({'referencia': 'Ingrese la referencia unica del movimiento verificada en el banco.'})
    return referencia


@transaction.atomic
def confirmar_recaudo(*, reporte_id, actor, cuenta_id, referencia, monto, fecha, evidencia_id, observacion='', justificacion_diferencia=''):
    habilitados()
    exigir_permiso(actor, PERMISO_CONCILIAR)
    reporte = ReporteRecaudoEducativo.objects.select_for_update().get(pk=reporte_id)
    if hasattr(reporte, 'rechazo'):
        raise ValidationError('El reporte ya fue rechazado; registre un reporte nuevo.')
    referencia = referencia_bancaria(referencia)
    try:
        monto = monto_cop(monto)
    except ValidationError as error:
        raise ValidationError({'monto': error.messages}) from None
    try:
        fecha = fecha_transferencia(fecha)
    except ValidationError as error:
        raise ValidationError({'fecha': error.messages}) from None
    observacion = (observacion or '').strip() or OBSERVACION_SIN_ADICIONAL
    justificacion_diferencia = justificacion_diferencia.strip()
    if monto != reporte.monto_reportado and not justificacion_diferencia:
        raise ValidationError({'justificacion_diferencia': 'Explique expresamente la diferencia entre monto recibido y reportado.'})
    # La cuenta viene de la version mostrada; el navegador no puede cambiarla.
    if str(cuenta_id) != str(reporte.receptor.cuenta_id):
        raise ValidationError('La cuenta no corresponde al receptor del reporte.')
    try:
        evidencia_id = uuid.UUID(str(evidencia_id))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError({'evidencia_id': 'Seleccione una evidencia bancaria disponible.'}) from None
    # Mismo orden para soportes compartidos; el escaner tambien bloquea la fila base.
    soportes = {archivo.pk: archivo for archivo in ArchivoRecaudoEducativo.objects.select_for_update().filter(
        pk__in=(evidencia_id, reporte.comprobante_id),
    ).order_by('pk')}
    evidencia = soportes.get(evidencia_id)
    if not evidencia:
        raise ValidationError({'evidencia_id': 'Evidencia no disponible. Recargue la lista.'})
    if evidencia.creado_por_id != actor.pk or evidencia.finalidad != 'BANK_EVIDENCE':
        raise PermissionDenied('Use la evidencia bancaria registrada por su cuenta.')
    if evidencia.pk == reporte.comprobante_id:
        raise ValidationError({'evidencia_id': 'La evidencia bancaria debe ser independiente del comprobante del pagador.'})
    comprobante = soportes[reporte.comprobante_id]
    if comprobante.estado_escaneo != 'SAFE':
        raise ValidationError('El comprobante debe superar el escaneo antes de confirmar.')
    if evidencia.estado_escaneo != 'SAFE':
        raise ValidationError({'evidencia_id': 'La evidencia debe superar el escaneo antes de confirmar.'})
    _verificar_integridad_soporte(comprobante)
    _verificar_integridad_soporte(evidencia, 'evidencia_id')
    anterior = ConciliacionRecaudoEducativo.objects.filter(reporte=reporte).first()
    if anterior:
        if (anterior.cuenta_id, anterior.referencia_verificada, anterior.monto_recibido,
                anterior.fecha_efectiva, anterior.evidencia_movimiento_id, anterior.observacion, anterior.justificacion_diferencia) != (
                cuenta_id, referencia, monto, fecha, evidencia_id, observacion, justificacion_diferencia):
            raise ValidationError('La conciliacion ya fue registrada con otros datos.')
        return anterior, True
    # Cuenta + referencia verificada identifica un movimiento global, no por tenant.
    CuentaRecaudoEducativa.objects.select_for_update().get(pk=cuenta_id)
    if ConciliacionRecaudoEducativo.objects.filter(cuenta_id=cuenta_id, referencia_verificada=referencia).exists():
        raise ValidationError({'referencia': 'El movimiento bancario ya esta conciliado. Revise la referencia.'})
    try:
        with transaction.atomic():
            conciliacion = ConciliacionRecaudoEducativo.objects.create(
                reporte=reporte, cuenta_id=cuenta_id, referencia_verificada=referencia,
                monto_recibido=monto, fecha_efectiva=fecha, evidencia_movimiento=evidencia,
                observacion=observacion, actor=actor,
                justificacion_diferencia=justificacion_diferencia,
            )
    except IntegrityError:
        raise ValidationError({'referencia': 'El movimiento bancario ya esta conciliado. Revise la referencia.'}) from None
    return conciliacion, False


@transaction.atomic
def rechazar_reporte(*, reporte_id, actor, motivo):
    habilitados()
    exigir_permiso(actor, PERMISO_CONCILIAR)
    reporte = ReporteRecaudoEducativo.objects.select_for_update().get(pk=reporte_id)
    if hasattr(reporte, 'conciliacion'):
        raise ValidationError('No se puede rechazar un ingreso confirmado.')
    if not motivo.strip():
        raise ValidationError('Indique un motivo claro y el siguiente paso para el pagador.')
    anterior = RechazoReporteRecaudoEducativo.objects.filter(reporte=reporte).first()
    if anterior:
        if anterior.motivo_publico != motivo.strip():
            raise ValidationError('El reporte ya tiene un motivo registrado.')
        return anterior
    return RechazoReporteRecaudoEducativo.objects.create(reporte=reporte, actor=actor, motivo_publico=motivo.strip())


@transaction.atomic
def _reclamar_escaneo(archivo_id, actor, motivo):
    archivo = ArchivoRecaudoEducativo.objects.select_for_update().get(pk=archivo_id)
    ultimo = archivo.escaneos.order_by('-numero').first()
    if ultimo and ultimo.estado in ('CLEAN', 'INFECTED'):
        return None
    ahora = timezone.now()
    if ultimo and ultimo.estado == 'STARTED':
        if ultimo.iniciado_en > ahora - timedelta(seconds=settings.FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS):
            return None
        ultimo.estado, ultimo.codigo_error, ultimo.finalizado_en = 'ERROR', 'ABANDONED_ATTEMPT', ahora
        ultimo._transicion_oficial = True
        ultimo.save()
    numero = (ultimo.numero if ultimo else 0) + 1
    if not actor and ultimo:
        if numero > settings.FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS:
            return None
        if ultimo.finalizado_en and ultimo.finalizado_en + timedelta(seconds=min(600, 30 * 2 ** min(ultimo.numero - 1, 5))) > ahora:
            return None
    return IntentoEscaneoRecaudo.objects.create(
        archivo=archivo, numero=numero, sha256=archivo.sha256, actor=actor, motivo_reintento=motivo,
    )


def escanear_archivo_recaudo(*, archivo_id, backend=None, actor=None, motivo=''):
    if actor:
        exigir_permiso(actor, PERMISO_ESCANEAR)
        if not motivo.strip():
            raise ValidationError('Registre el motivo del reintento manual.')
    intento = _reclamar_escaneo(archivo_id, actor, motivo.strip())
    if not intento:
        return None
    estado, error = 'ERROR', ''
    try:
        with intento.archivo.archivo.open('rb') as archivo:
            contenido = archivo.read(settings.FINANCIACION_EDUCATIVA_RECAUDO_MAX_BYTES + 1)
        if len(contenido) != intento.archivo.tamano_bytes or hashlib.sha256(contenido).hexdigest() != intento.sha256:
            raise ErrorEscaneoDocumento('CONTENT_CHANGED')
        resultado = (backend or obtener_backend_escaneo()).escanear(BytesIO(contenido))
        if not isinstance(resultado, ResultadoAntivirus) or resultado.veredicto not in VeredictoAntivirus:
            raise ErrorEscaneoDocumento('INVALID_RESPONSE')
        estado = 'CLEAN' if resultado.veredicto == VeredictoAntivirus.CLEAN else 'INFECTED'
    except ErrorEscaneoDocumento as exc:
        error = exc.codigo if exc.codigo in {'CONTENT_CHANGED', 'SCANNER_TIMEOUT', 'SCANNER_UNAVAILABLE', 'INVALID_RESPONSE'} else 'SCANNER_ERROR'
    except Exception:
        error = 'SCAN_TECHNICAL_ERROR'
    with transaction.atomic():
        ArchivoRecaudoEducativo.objects.select_for_update().get(pk=archivo_id)
        actual = IntentoEscaneoRecaudo.objects.select_for_update().get(pk=intento.pk)
        if actual.estado != 'STARTED':
            return actual
        actual.estado, actual.codigo_error, actual.finalizado_en = estado, error, timezone.now()
        actual._transicion_oficial = True
        actual.save()
        return actual
