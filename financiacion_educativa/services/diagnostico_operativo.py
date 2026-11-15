"""Aggregate-only operational observations. Never calls recovery or providers."""
from datetime import timedelta
import re
import subprocess

from django.conf import settings
from django.db.models import Count, Min, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from financiacion_educativa.choices import EtapaAutomatizacionEducativa
from financiacion_educativa.models import (
    EventoWebhookFirmaEducativa, OutboxCorreoEducativo, ProcesoAutomatizacionEducativa,
    ProcesoFirmaEducativa,
)
from financiacion_educativa.services.cola_automatizacion import ESTADOS_RECLAMABLES as ESTADOS_RECLAMABLES_AUTOMATIZACION
from financiacion_educativa.services.outbox_correos import CODIGO_PLAN, ESTADOS_RECLAMABLES, filtro_pausa_breb
from financiacion_educativa.services.pausa_firma import firma_pausada


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

    limite_cola = ahora - timedelta(minutes=limites['aviso_minutos'])
    flag_breb = settings.FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED
    flag_plan = settings.FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED
    auto_habilitada = settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED
    firma_en_pausa = firma_pausada()

    correos = OutboxCorreoEducativo.objects.all()
    correos_vencidos = correos.filter(estado__in=ESTADOS_RECLAMABLES, proxima_ejecucion_en__lte=limite_cola)
    correos_accionables = correos_vencidos
    avisos_pausados = {}
    if not flag_breb:
        filtro_breb = filtro_pausa_breb()
        avisos_pausados['breb'] = correos_vencidos.filter(filtro_breb).count()
        correos_accionables = correos_accionables.exclude(filtro_breb)
    if not flag_plan:
        avisos_pausados['plan_contractual'] = correos_vencidos.filter(codigo_mensaje=CODIGO_PLAN).count()
        correos_accionables = correos_accionables.exclude(codigo_mensaje=CODIGO_PLAN)
    contar(correos_accionables,
        'OUTBOX_OVERDUE', 'proxima_ejecucion_en', 'WARNING', 'Soporte APROBADO',
        'Revisar servicio outbox, flags y proxima ejecucion; no reenviar ni ejecutar el procesador desde el diagnostico.')
    contar(correos.filter(estado='SENDING').filter(Q(lease_vence_en__lte=ahora) | Q(lease_vence_en__isnull=True)),
        'OUTBOX_LEASE_EXPIRED', 'ultimo_intento_en', 'CRITICAL', 'Soporte APROBADO + Infraestructura',
        'Revisar lease y registros SMTP; conciliar posible entrega antes de cualquier recuperacion autorizada.')
    contar(correos.filter(estado='FAILED'), 'OUTBOX_FAILED', 'actualizada_en', 'CRITICAL', 'Soporte APROBADO',
        'Consultar codigo de error en Admin restringido; corregir causa y aprobar recuperacion separada, nunca reenviar a ciegas.')
    contar(correos.filter(estado='AMBIGUOUS'), 'OUTBOX_AMBIGUOUS', 'actualizada_en', 'CRITICAL', 'Soporte APROBADO',
        'Conciliar Message-ID con SMTP/proveedor antes de resolver; no reintentar automaticamente.')

    automatizacion = ProcesoAutomatizacionEducativa.objects.all()
    cola = automatizacion.filter(estado__in=ESTADOS_RECLAMABLES_AUTOMATIZACION, proxima_ejecucion_en__lte=limite_cola)
    etapa_firma = EtapaAutomatizacionEducativa.SIGNATURE_SEND
    if not auto_habilitada:
        automatizacion_pausada = cola.count()
        cola_accionable = cola.none()
    elif firma_en_pausa:
        automatizacion_pausada = cola.filter(etapa_actual=etapa_firma).count()
        cola_accionable = cola.exclude(etapa_actual=etapa_firma)
    else:
        automatizacion_pausada = 0
        cola_accionable = cola
    contar(cola_accionable, 'AUTOMATION_QUEUE_OVERDUE', 'proxima_ejecucion_en', 'WARNING',
        'Operaciones APROBADO + Infraestructura',
        'Revisar worker educativo y la etapa en cola; distinguir escaneo, validacion y contrato antes de intervenir.')
    contar(automatizacion.filter(estado='RUNNING', lease_vence_en__lte=ahora),
        'AUTOMATION_LEASE_EXPIRED', 'lease_vence_en', 'CRITICAL', 'Operaciones APROBADO + Infraestructura',
        'Comprobar worker, etapa y posible efecto externo antes de recuperar el proceso.')

    firmas = ProcesoFirmaEducativa.objects.filter(artefacto__vigente=True)
    firmas_vencidas = firmas.filter(estado='PENDING', creado_en__lte=limite_cola)
    firmas_pausadas = firmas_vencidas.count() if firma_en_pausa else 0
    if not firma_en_pausa:
        contar(firmas_vencidas, 'SIGNATURE_QUEUE_OVERDUE', 'creado_en', 'WARNING', 'Operaciones APROBADO',
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
    planes = list(correos.filter(codigo_mensaje=CODIGO_PLAN).values('estado').annotate(
        cantidad=Count('pk')).order_by('estado'))
    etapas_atrasadas = list(cola_accionable.values('etapa_actual').annotate(
        cantidad=Count('pk')).order_by('etapa_actual'))
    return {'evaluado_en': ahora.isoformat(), 'umbrales': limites, 'alertas': alertas,
        'outbox_por_estado': estados, 'avisos_breb': breb, 'avisos_plan_contractual': planes,
        'backlog_pausado': {'avisos': avisos_pausados, 'automatizacion': automatizacion_pausada,
            'firmas': firmas_pausadas},
        'automatizacion_atrasada_por_etapa': etapas_atrasadas,
        'flags_observados': {'libro': settings.FINANCIACION_EDUCATIVA_LIBRO_ENABLED,
            'avisos_breb': flag_breb, 'avisos_plan_contractual': flag_plan,
            'automatizacion': auto_habilitada, 'envio_firma_pausado': firma_en_pausa},
        'sent_no_acredita_entrega_en_buzon_ni_primer_envio_real': True}
