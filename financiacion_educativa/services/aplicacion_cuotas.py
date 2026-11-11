from django.core.exceptions import ValidationError
from django.core import signing
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.models import SolicitudFinanciacionEducativa, CuotaAmortizacionEducativa
from financiacion_educativa.models_recaudos import (
    AplicacionCuotaEducativa, ArchivoRecaudoEducativo,
    ConciliacionRecaudoEducativo, ReporteRecaudoEducativo,
)
from financiacion_educativa.services.recaudos import (
    PERMISO_CONSULTAR, exigir_permiso, habilitados, leer_soporte_seguro,
)
from financiacion_educativa.dashboards.operaciones.permissions import PERMISO_ACCESO

PERMISO_APLICAR = 'financiacion_educativa.aplicar_cuotas_educativas'
VISTA_PREVIA_SALT = 'educational-installment-application-v1'
VISTA_PREVIA_MAX_AGE = 1800


def exigir_operador(actor):
    for permiso in (PERMISO_ACCESO, PERMISO_CONSULTAR, PERMISO_APLICAR):
        exigir_permiso(actor, permiso)


def _snapshot(reporte, cuota, actor):
    ingreso = reporte.conciliacion
    return {
        'reporte': str(reporte.pk), 'ingreso': str(ingreso.pk),
        'cuota': str(cuota.pk), 'fotografia': str(cuota.fotografia_id),
        'numero': cuota.numero, 'vencimiento': cuota.fecha_vencimiento.isoformat(),
        'capital': str(cuota.capital), 'interes': str(cuota.interes),
        'importe': str(cuota.valor_cuota), 'recibido': str(ingreso.monto_recibido),
        'fecha_bancaria': ingreso.fecha_efectiva.isoformat(), 'actor': str(actor.pk),
    }


def preparar_vista_previa(*, reporte, actor):
    habilitados()
    exigir_operador(actor)
    cuota = previsualizar_cuota(reporte)
    return cuota, signing.dumps(_snapshot(reporte, cuota, actor), salt=VISTA_PREVIA_SALT)


def _primera_cuota_pendiente(reporte):
    cuota = reporte.fotografia.cuotas.filter(
        aplicacion_recaudo__isnull=True,
    ).order_by('numero').first()
    if cuota is None:
        raise ValidationError('No quedan cuotas pendientes en este calendario.')
    return cuota


def previsualizar_cuota(reporte):
    ingreso = getattr(reporte, 'conciliacion', None)
    if ingreso is None or hasattr(reporte, 'rechazo'):
        raise ValidationError('Se requiere un ingreso conciliado.')
    if reporte.proposito != ReporteRecaudoEducativo.Proposito.INSTALLMENT:
        raise ValidationError('Esta operación solo admite pagos de cuota.')
    if not obligaciones_autorizadas().filter(
        pk=reporte.fotografia_id, solicitud_id=reporte.solicitud_id,
    ).exists():
        raise ValidationError('El reporte no corresponde al calendario firmado vigente.')
    if ingreso.fecha_efectiva > timezone.localdate():
        raise ValidationError('La fecha efectiva bancaria no puede ser futura.')
    if ingreso.monto_recibido != reporte.monto_reportado:
        raise ValidationError('Existe una diferencia con el monto reportado. Requiere revision operativa antes de aplicar.')
    if ingreso.cuenta_id != reporte.receptor.cuenta_id:
        raise ValidationError('La cuenta conciliada no corresponde a este reporte.')
    comprobante, evidencia = reporte.comprobante, ingreso.evidencia_movimiento
    if (comprobante.finalidad != 'RECEIPT'
            or comprobante.creado_por_id != reporte.reportado_por_id
            or evidencia.finalidad != 'BANK_EVIDENCE'
            or evidencia.creado_por_id != ingreso.actor_id
            or comprobante.pk == evidencia.pk):
        raise ValidationError('La evidencia bancaria o el comprobante no corresponden al ingreso.')
    leer_soporte_seguro(comprobante)
    leer_soporte_seguro(evidencia)

    cuota = _primera_cuota_pendiente(reporte)
    if (ingreso.monto_recibido != cuota.valor_cuota
            or cuota.capital + cuota.interes != cuota.valor_cuota
            or cuota.capital <= 0 or cuota.interes < 0):
        raise ValidationError(
            'El ingreso debe cubrir exactamente la primera cuota pendiente. '
            'Las diferencias requieren revisión operativa.'
        )
    return cuota


@transaction.atomic
def aplicar_cuota(*, reporte_id, cuota_id, actor, vista_previa, confirmar_cuota):
    habilitados()
    exigir_operador(actor)
    if confirmar_cuota is not True:
        raise ValidationError('Confirma que esta es la cuota que el estudiante quiso pagar.')
    try:
        snapshot = signing.loads(vista_previa, salt=VISTA_PREVIA_SALT, max_age=VISTA_PREVIA_MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError):
        raise ValidationError('La vista previa vencio o no es valida. Recarga el reporte.') from None
    # Orden común para todos los ingresos de la obligación, incluso reportes distintos.
    solicitud_id = ReporteRecaudoEducativo.objects.values_list(
        'solicitud_id', flat=True,
    ).get(pk=reporte_id)
    SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=solicitud_id)
    reporte = ReporteRecaudoEducativo.objects.select_for_update().get(pk=reporte_id)
    ingreso = ConciliacionRecaudoEducativo.objects.select_for_update().filter(reporte=reporte).first()
    if ingreso is None:
        raise ValidationError('Se requiere un ingreso conciliado.')
    reporte.conciliacion = ingreso
    existente = getattr(ingreso, 'aplicacion', None)
    if existente:
        if existente.cuota_id != cuota_id:
            raise ValidationError('Este ingreso ya se aplicó a otra cuota.')
        if snapshot != _snapshot(reporte, existente.cuota, actor):
            raise ValidationError('La vista previa no corresponde a este ingreso y operador. Recarga el reporte.')
        return existente

    if _primera_cuota_pendiente(reporte).pk != cuota_id:
        raise ValidationError('La cuota cambio. Recarga el reporte antes de confirmar.')
    # Same file-lock order as reconciliation and antivirus; no nullable joins.
    list(ArchivoRecaudoEducativo.objects.select_for_update().filter(
        pk__in=(reporte.comprobante_id, ingreso.evidencia_movimiento_id),
    ).order_by('pk'))
    cuota = previsualizar_cuota(reporte)
    cuota = CuotaAmortizacionEducativa.objects.select_for_update().get(pk=cuota.pk)
    if cuota.pk != cuota_id or snapshot != _snapshot(reporte, cuota, actor):
        raise ValidationError('La cuota cambió. Recarga el reporte antes de confirmar.')
    return AplicacionCuotaEducativa.objects.create(
        conciliacion=ingreso, cuota=cuota, capital_aplicado=cuota.capital,
        interes_aplicado=cuota.interes, fecha_efectiva=ingreso.fecha_efectiva, actor=actor,
        cuota_verificada=True,
    )
