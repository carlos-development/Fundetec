"""Aggregate-only operational observations. Never calls recovery or providers."""
from datetime import timedelta
import re
import subprocess

from django.conf import settings
from django.db.models import Count, Min, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from financiacion_educativa.models import EventoWebhookFirmaEducativa, OutboxCorreoEducativo, ProcesoFirmaEducativa


UMBRALES = {'aviso_minutos': 15, 'firma_envio_minutos': 10, 'firma_espera_horas': 72}
EVENTOS_BREB = ('BREB_REPORT_RECEIVED', 'BREB_INCOME_CONFIRMED', 'BREB_PAYMENT_APPLIED')


def alerta(codigo, cantidad, severidad, responsable, primer_paso, edad=None):
    return {'codigo': codigo, 'cantidad': cantidad, 'severidad': severidad,
        'responsable': responsable, 'primer_paso': primer_paso,
        'antiguedad_maxima_segundos': edad}


def validar_unidades(unidades):
    if len(unidades) > 16 or any(not re.fullmatch(r'[A-Za-z0-9_@.-]+\.(service|timer)', u) for u in unidades):
        raise ValueError('Indique hasta 16 nombres systemd service/timer, sin rutas ni opciones.')
    return list(dict.fromkeys(unidades))


def comprobar_servicios(unidades):
    observaciones, alertas = [], []
    for unidad in validar_unidades(unidades):
        try:
            resultado = subprocess.run(['systemctl', 'show', '--no-pager',
                '--property=LoadState,ActiveState,SubState,Result', '--', unidad],
                capture_output=True, text=True, timeout=5, check=False)
            datos = dict(linea.split('=', 1) for linea in resultado.stdout.splitlines() if '=' in linea)
            disponible = resultado.returncode == 0 and datos.get('LoadState') == 'loaded'
            activo = disponible and datos.get('ActiveState') == 'active'
        except (OSError, subprocess.TimeoutExpired, ValueError):
            datos, disponible, activo = {}, False, False
        observaciones.append({'unidad': unidad, 'disponible': disponible, 'activa': activo})
        if not activo:
            alertas.append(alerta('SERVICE_DOWN' if disponible else 'SERVICE_CHECK_UNAVAILABLE', 1,
                'CRITICAL', 'Infraestructura de guardia',
                f'Consultar systemctl status {unidad} --no-pager y journal local; no reiniciar desde el diagnostico.'))
    if not unidades:
        alertas.append(alerta('SERVICE_SCOPE_MISSING', 1, 'WARNING', 'Infraestructura de guardia',
            'Repetir con --servicio para cada unidad realmente requerida; este resultado no certifica disponibilidad.'))
    return observaciones, alertas


def observar_base(*, umbrales=None, ahora=None):
    limites = {**UMBRALES, **(umbrales or {})}
    if any(type(v) is not int or not 1 <= v <= 10080 for v in limites.values()):
        raise ValueError('Los umbrales deben ser enteros positivos hasta 10080.')
    ahora = ahora or timezone.now()
    alertas = []

    def contar(queryset, codigo, campo, severidad, responsable, paso):
        resumen = queryset.aggregate(cantidad=Count('pk'), primero=Min(campo))
        if resumen['cantidad']:
            edad = max(0, int((ahora - resumen['primero']).total_seconds())) if resumen['primero'] else None
            alertas.append(alerta(codigo, resumen['cantidad'], severidad, responsable, paso, edad))

    correos = OutboxCorreoEducativo.objects.all()
    contar(correos.filter(estado__in=['PENDING', 'RETRYING'],
        proxima_ejecucion_en__lte=ahora - timedelta(minutes=limites['aviso_minutos'])),
        'OUTBOX_OVERDUE', 'proxima_ejecucion_en', 'WARNING', 'Soporte APROBADO',
        'Revisar servicio outbox, flags y proxima ejecucion; no reenviar ni ejecutar el procesador desde el diagnostico.')
    contar(correos.filter(estado='SENDING').filter(Q(lease_vence_en__lte=ahora) | Q(lease_vence_en__isnull=True)),
        'OUTBOX_LEASE_EXPIRED', 'ultimo_intento_en', 'CRITICAL', 'Soporte APROBADO + Infraestructura',
        'Revisar lease y registros SMTP; conciliar posible entrega antes de cualquier recuperacion autorizada.')
    contar(correos.filter(estado='FAILED'), 'OUTBOX_FAILED', 'actualizada_en', 'CRITICAL', 'Soporte APROBADO',
        'Consultar codigo de error en Admin restringido; corregir causa y aprobar recuperacion separada, nunca reenviar a ciegas.')
    contar(correos.filter(estado='AMBIGUOUS'), 'OUTBOX_AMBIGUOUS', 'actualizada_en', 'CRITICAL', 'Soporte APROBADO',
        'Conciliar Message-ID con SMTP/proveedor antes de resolver; no reintentar automaticamente.')

    firmas = ProcesoFirmaEducativa.objects.filter(artefacto__vigente=True)
    contar(firmas.filter(estado='PENDING', creado_en__lte=ahora - timedelta(minutes=limites['aviso_minutos'])),
        'SIGNATURE_QUEUE_OVERDUE', 'creado_en', 'WARNING', 'Operaciones APROBADO',
        'Revisar etapa y autorizacion del expediente; no ejecutar envio ni reintentar firmas.')
    contar(firmas.filter(estado='SENDING').annotate(inicio=Coalesce('envio_iniciado_en', 'actualizado_en')).filter(
        inicio__lte=ahora - timedelta(minutes=limites['firma_envio_minutos'])),
        'SIGNATURE_SEND_STALLED', 'inicio', 'CRITICAL', 'Operaciones APROBADO + Infraestructura',
        'Consultar estado y external_id en acceso restringido; conciliar con ZapSign antes de otro envio.')
    ambiguas = Q(codigo_ultimo_error='SIGNATURE_SEND_AMBIGUOUS')
    contar(firmas.filter(estado='FAILED').filter(ambiguas), 'SIGNATURE_AMBIGUOUS', 'actualizado_en',
        'CRITICAL', 'Operaciones APROBADO',
        'Conciliar resultado externo incierto con ZapSign; no reenviar contrato ni regenerarlo.')
    contar(firmas.filter(estado='FAILED').exclude(ambiguas), 'SIGNATURE_FAILED', 'actualizado_en',
        'CRITICAL', 'Operaciones APROBADO',
        'Consultar codigo y evidencia tecnica restringidos; no cambiar el contrato ni el estado manualmente.')
    contar(firmas.filter(estado='SENT').annotate(inicio=Coalesce('enviado_en', 'actualizado_en')).filter(
        inicio__lte=ahora - timedelta(hours=limites['firma_espera_horas'])),
        'SIGNATURE_WAIT_OVERDUE', 'inicio', 'WARNING', 'Operaciones APROBADO',
        'Comprobar si espera al firmante o falta webhook; no implica rechazo ni autoriza un reenvio.')
    contar(EventoWebhookFirmaEducativa.objects.filter(estado__in=['RECEIVED', 'RETRYABLE_ERROR'],
        recibido_en__lte=ahora - timedelta(minutes=limites['aviso_minutos'])),
        'SIGNATURE_WEBHOOK_OVERDUE', 'recibido_en', 'CRITICAL', 'Operaciones APROBADO + Infraestructura',
        'Revisar el evento y su resultado en acceso restringido; no recrear la firma ni autorizar el curso manualmente.')
    estados = {fila['estado']: fila['cantidad'] for fila in correos.values('estado').annotate(cantidad=Count('pk'))}
    breb = list(correos.filter(tipo_evento__in=EVENTOS_BREB).values('tipo_evento', 'estado').annotate(
        cantidad=Count('pk')).order_by('tipo_evento', 'estado'))
    return {'evaluado_en': ahora.isoformat(), 'umbrales': limites, 'alertas': alertas,
        'outbox_por_estado': estados, 'avisos_breb': breb,
        'flags_observados': {'libro': settings.FINANCIACION_EDUCATIVA_LIBRO_ENABLED,
            'avisos_breb': settings.FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED},
        'sent_no_acredita_entrega_en_buzon_ni_primer_envio_real': True}
