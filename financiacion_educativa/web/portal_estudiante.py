"""Consultas y presentacion del portal propio; sin mutaciones financieras."""

from datetime import date
from decimal import Decimal
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.models_recaudos import ReporteRecaudoEducativo
from financiacion_educativa.services.reanudacion import resolver_destino_reanudacion
from financiacion_educativa.services.seguimiento_cartera import etiqueta_vencimiento
from financiacion_educativa.services.libro_pagos import anotar_cuotas, anotar_reportes


def calendario_del_portal(fotografia):
    cuotas = list(anotar_cuotas(fotografia.cuotas.all()).annotate(
        aplicada=F('cubierta_libro'),
    ).order_by('numero').values(
        'numero', 'fecha_vencimiento', 'valor_cuota', 'capital', 'interes', 'aplicada',
    ))
    primera = next((c for c in cuotas if not c['aplicada']), None)
    estado = fotografia.movimientos_libro.order_by('-secuencia').values_list('estado_despues', flat=True).first()
    proyectado = bool(estado and estado['proyectado'])
    if proyectado:
        fila = estado['plan'][0] if estado['plan'] else None
        primera = {'numero': fila['numero'], 'fecha_vencimiento': date.fromisoformat(fila['fecha']),
            'valor_cuota': Decimal(fila['capital']) + Decimal(fila['interes'])} if fila else None
    return {
        'plan_libro_modificado': proyectado,
        'cuotas_portal': cuotas,
        'primera_cuota_pendiente': primera,
        'etiqueta_primera_cuota': etiqueta_vencimiento(primera['fecha_vencimiento'], timezone.localdate()) if primera else '',
        'cuotas_aplicadas': sum(c['aplicada'] for c in cuotas),
    }


def solicitudes_del_portal(usuario):
    autorizadas = {
        f.solicitud_id: f for f in obligaciones_autorizadas().filter(solicitud__usuario=usuario)
    }
    grupos = {'tramites': [], 'financiaciones': [], 'cerradas': []}
    solicitudes = SolicitudFinanciacionEducativa.objects.filter(
        usuario=usuario,
    ).select_related('institucion').order_by('-creada_en', '-pk')
    for solicitud in solicitudes:
        destino = resolver_destino_reanudacion(solicitud)
        fotografia = autorizadas.get(solicitud.pk)
        pendiente_autorizacion = solicitud.estado == 'APPROVED' and fotografia is None
        opcion = {
            'solicitud': solicitud, 'destino': destino,
            'autorizada': fotografia is not None,
            'pendiente_autorizacion': pendiente_autorizacion,
        }
        if fotografia:
            opcion.update(calendario_del_portal(fotografia))
            opcion['url'] = reverse('financiacion_educativa_web:finanzas', args=[solicitud.pk])
            grupos['financiaciones'].append(opcion)
        else:
            # Never offer the financial portal solely on the internal state.
            ruta = destino.ruta
            if ruta == 'financiacion_educativa_web:finanzas':
                ruta = 'financiacion_educativa_web:estado-solicitud'
            opcion['accion'] = 'Consultar estado' if ruta == 'financiacion_educativa_web:estado-solicitud' else destino.accion
            opcion['url'] = reverse(ruta, args=[solicitud.pk])
            grupos['tramites' if destino.inconclusa or pendiente_autorizacion else 'cerradas'].append(opcion)
    return grupos


def contexto_financiacion_del_portal(fotografia):
    # Whitelist public fields at the query boundary. No bank reference, evidence,
    # file path, hash or internal actor is loaded into the history presentation.
    filas = anotar_reportes(ReporteRecaudoEducativo.objects.filter(
        solicitud_id=fotografia.solicitud_id, fotografia=fotografia,
    )).order_by('-creado_en', '-pk').values(
        'id', 'monto_reportado', 'fecha_declarada',
        'conciliacion__monto_recibido', 'conciliacion__fecha_efectiva',
        'conciliacion__aplicacion__cuota__numero', 'rechazo__motivo_publico',
        'dinero_aplicado', 'dinero_sin_destino',
    )
    historial = []
    hay_confirmadas_por_aplicar = False
    hay_reportes_por_verificar = False
    for fila in filas:
        numero = fila['conciliacion__aplicacion__cuota__numero']
        confirmada = fila['conciliacion__monto_recibido'] is not None
        hay_confirmadas_por_aplicar |= confirmada and fila['dinero_sin_destino'] > 0
        hay_reportes_por_verificar |= not confirmada and not fila['rechazo__motivo_publico']
        historial.append({
            'estado': ('Pago aplicado' if numero is not None else
                       'Aplicacion contabilizada' if fila['dinero_aplicado'] > 0 else
                       'Transferencia confirmada' if confirmada else
                       'Reporte no conciliado' if fila['rechazo__motivo_publico'] else
                       'En verificación'),
            'aplicado': numero is not None or fila['dinero_aplicado'] > 0, 'confirmado': confirmada,
            'cuota': numero,
            'monto': fila['dinero_aplicado'] if fila['dinero_aplicado'] > 0 else fila['conciliacion__monto_recibido'] if confirmada else fila['monto_reportado'],
            'fecha': fila['conciliacion__fecha_efectiva'] if confirmada else fila['fecha_declarada'],
            'url': reverse('financiacion_educativa_web:reporte-pago', args=[fotografia.solicitud_id, fila['id']]),
        })
    return {
        'portal_financiacion': True, 'fotografia': fotografia,
        **calendario_del_portal(fotografia),
        'historial_portal': historial,
        'hay_confirmadas_por_aplicar': hay_confirmadas_por_aplicar,
        'hay_reportes_por_verificar': hay_reportes_por_verificar,
    }
