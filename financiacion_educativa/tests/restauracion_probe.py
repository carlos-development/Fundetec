"""Probe for a restored synthetic DB, executed in a separate Django process."""
import hashlib
import json
import os
import traceback

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from financiacion_educativa.models import ArtefactoContractualEducativo, OutboxCorreoEducativo, SolicitudFinanciacionEducativa
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.libro_pagos import anotar_ingresos, resumen_ingresos


def firma_calendario(solicitud):
    cuotas = list(solicitud.fotografias_financieras.filter(activa=True).get().cuotas.order_by('numero').values(
        'numero', 'fecha_vencimiento', 'saldo_inicial', 'capital', 'interes', 'valor_cuota', 'saldo_final'))
    return hashlib.sha256(json.dumps(cuotas, sort_keys=True, default=str).encode()).hexdigest()


def publico_resumido(solicitud):
    resultado = obtener_resultado_publico(solicitud)
    return {'status': resultado.estado, 'course_authorized': resultado.curso_autorizado,
        'financial_terms': resultado.condiciones_financieras}


def verificar():
    db = settings.DATABASES['default']
    if (not __debug__ or settings.DEPLOYMENT_ENVIRONMENT != 'test' or db['HOST'] != '127.0.0.1'
            or not db['NAME'].startswith('restore_') or os.environ.get('EDU_RESTORE_PROBE') != 'synthetic-only'):
        raise RuntimeError('El probe solo puede ejecutarse en el destino sintetico aislado.')
    esperado = json.loads(os.environ['EDU_RESTORE_EXPECTED'])
    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=os.environ['EDU_RESTORE_APPLICATION'])
    publico = publico_resumido(solicitud)
    assert publico == esperado['publico']
    assert publico['status'] == 'APPROVED' and publico['course_authorized'] is True and publico['financial_terms']
    artefacto = ArtefactoContractualEducativo.objects.get(solicitud=solicitud, tipo='PROMISSORY_NOTE', vigente=True)
    with artefacto.archivo_firmado.open('rb') as archivo:
        assert hashlib.sha256(archivo.read()).hexdigest() == artefacto.hash_firmado_sha256
    assert firma_calendario(solicitud) == esperado['calendario']
    ingresos = ConciliacionRecaudoEducativo.objects.filter(reporte__solicitud=solicitud)
    assert ingresos.count() == 2
    assert AplicacionCuotaEducativa.objects.filter(conciliacion__in=ingresos).count() == 1
    assert OutboxCorreoEducativo.objects.count() == esperado['outbox_count']
    totales = resumen_ingresos(ingresos)
    assert {k: str(v) for k, v in totales.items()} == esperado['totales']
    assert totales['recibido'] == totales['capital'] + totales['intereses'] + totales['sin_destino']
    for ingreso in anotar_ingresos(ingresos):
        assert ingreso.monto_recibido == ingreso.capital_neto + ingreso.interes_neto + ingreso.disponible_libro
    cliente = Client()
    cliente.force_login(solicitud.usuario)
    ruta = reverse('financiacion_educativa_web:pago-comprobante', args=[solicitud.pk, os.environ['EDU_RESTORE_REPORT']])
    respuesta = cliente.get(ruta)
    assert respuesta.status_code == 200
    try:
        contenido = b''.join(respuesta.streaming_content)
    finally:
        if respuesta.file_to_stream:
            respuesta.file_to_stream.close()
    assert hashlib.sha256(contenido).hexdigest() == esperado['comprobante']
    assert 'no-store' in respuesta['Cache-Control']
    assert cliente.get(reverse('financiacion_educativa_web:operaciones:recaudo-soporte',
        args=[os.environ['EDU_RESTORE_REPORT']]) + '?banco=1').status_code == 403
    cliente.force_login(get_user_model().objects.get(username='cartera'))
    assert cliente.get(ruta).status_code == 404
    print('RESTORE_PROBE=' + json.dumps({'contrato': 'APPROVED', 'curso_autorizado': True,
        'privado_legible': True, 'titularidad': True, 'evidencia_bancaria_protegida': True,
        'calendario_intacto': True, 'pagare_sintetico_intacto': True, 'aplicacion_historica_unica': True,
        'recaudos_cuadran': True, 'totales': esperado['totales']}, sort_keys=True))


def ejecutar_probe():
    try:
        verificar()
    except Exception as error:
        marcos = [m for m in traceback.extract_tb(error.__traceback__) if m.filename.endswith('restauracion_probe.py')]
        print('RESTORE_PROBE_FAILURE=' + json.dumps({'tipo': type(error).__name__,
            'linea_probe': marcos[-1].lineno if marcos else None}))
        raise RuntimeError('Verificador sintetico fallido; solo se informa tipo y ubicacion.') from None
