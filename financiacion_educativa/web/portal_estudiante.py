"""Consultas y presentacion del portal propio; sin mutaciones financieras."""

from django.db.models import Exists, OuterRef
from django.urls import reverse

from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ReporteRecaudoEducativo
from financiacion_educativa.services.reanudacion import resolver_destino_reanudacion


def calendario_del_portal(fotografia):
    cuotas = list(fotografia.cuotas.annotate(
        aplicada=Exists(AplicacionCuotaEducativa.objects.filter(cuota_id=OuterRef('pk'))),
    ).order_by('numero').values(
        'numero', 'fecha_vencimiento', 'valor_cuota', 'capital', 'interes', 'aplicada',
    ))
    return {
        'cuotas_portal': cuotas,
        'primera_cuota_pendiente': next((c for c in cuotas if not c['aplicada']), None),
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
    filas = ReporteRecaudoEducativo.objects.filter(
        solicitud_id=fotografia.solicitud_id, fotografia=fotografia,
    ).order_by('-creado_en', '-pk').values(
        'id', 'monto_reportado', 'fecha_declarada',
        'conciliacion__monto_recibido', 'conciliacion__fecha_efectiva',
        'conciliacion__aplicacion__cuota__numero', 'rechazo__motivo_publico',
    )
    historial = []
    hay_confirmadas_por_aplicar = False
    for fila in filas:
        numero = fila['conciliacion__aplicacion__cuota__numero']
        confirmada = fila['conciliacion__monto_recibido'] is not None
        hay_confirmadas_por_aplicar |= confirmada and numero is None
        historial.append({
            'estado': ('Pago aplicado' if numero is not None else
                       'Transferencia confirmada' if confirmada else
                       'Reporte no conciliado' if fila['rechazo__motivo_publico'] else
                       'En verificación'),
            'aplicado': numero is not None, 'confirmado': confirmada,
            'cuota': numero,
            'monto': fila['conciliacion__monto_recibido'] if confirmada else fila['monto_reportado'],
            'fecha': fila['conciliacion__fecha_efectiva'] if confirmada else fila['fecha_declarada'],
            'url': reverse('financiacion_educativa_web:reporte-pago', args=[fotografia.solicitud_id, fila['id']]),
        })
    return {
        'portal_financiacion': True, 'fotografia': fotografia,
        **calendario_del_portal(fotografia),
        'historial_portal': historial,
        'hay_confirmadas_por_aplicar': hay_confirmadas_por_aplicar,
    }
