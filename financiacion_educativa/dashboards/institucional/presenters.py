from financiacion_educativa.choices import (
    EstadoPublicoSolicitud,
    EstadoSolicitudFinanciacion,
    TipoArtefactoContractualEducativo,
)
from financiacion_educativa.services.estado_publico import (
    MAPA_ESTADO_PUBLICO,
    obtener_resultado_publico,
)
from instituciones.models import MembresiaInstitucion


SEGUIMIENTO_POR_ESTADO = {
    EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION: 'Pendiente de iniciar',
    EstadoSolicitudFinanciacion.PENDING_TERMS: 'Aceptacion de terminos',
    EstadoSolicitudFinanciacion.PENDING_DOCUMENT: 'Expediente documental',
    EstadoSolicitudFinanciacion.PENDING_GUARDIAN: 'Registro de tutor',
    EstadoSolicitudFinanciacion.CORRECTION_REQUIRED: 'Correccion requerida',
    EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW: 'Revision adicional',
    EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE: 'Preparacion contractual',
    EstadoSolicitudFinanciacion.PENDING_SIGNATURE: 'Pendiente de firma',
    EstadoSolicitudFinanciacion.APPROVED: 'Curso autorizado',
    EstadoSolicitudFinanciacion.REJECTED: 'Solicitud rechazada',
    EstadoSolicitudFinanciacion.CANCELLED: 'Solicitud cancelada',
    EstadoSolicitudFinanciacion.ACTIVE: 'Financiacion activa',
    EstadoSolicitudFinanciacion.PAYMENT_REPORTED: 'Pago reportado',
    EstadoSolicitudFinanciacion.PAYMENT_UNDER_REVIEW: 'Pago en revision',
    EstadoSolicitudFinanciacion.PAID: 'Financiacion pagada',
}


def _estado_publico(estado_interno):
    codigo = MAPA_ESTADO_PUBLICO.get(
        estado_interno,
        EstadoPublicoSolicitud.UNDER_REVIEW,
    )
    return {
        'codigo': codigo,
        'etiqueta': EstadoPublicoSolicitud(codigo).label,
        'clase': codigo.lower().replace('_', '-'),
    }


def enmascarar_documento(valor):
    texto = ''.join(caracter for caracter in str(valor or '') if caracter.isalnum())
    if not texto:
        return 'No registrado'
    visibles = min(4, len(texto))
    return f'{"*" * max(4, len(texto) - visibles)}{texto[-visibles:]}'


def enmascarar_correo(valor):
    texto = str(valor or '').strip()
    if '@' not in texto:
        return 'No registrado'
    local, dominio = texto.rsplit('@', 1)
    inicial = local[:1] or '*'
    return f'{inicial}***@{dominio}'


def enmascarar_telefono(valor):
    texto = ''.join(caracter for caracter in str(valor or '') if caracter.isdigit())
    if not texto:
        return 'No registrado'
    visibles = min(4, len(texto))
    return f'{"*" * max(4, len(texto) - visibles)}{texto[-visibles:]}'


def presentar_resumen_solicitud(solicitud):
    return {
        'id': solicitud.pk,
        'referencia': solicitud.referencia_externa,
        'solicitante': f'{solicitud.nombres} {solicitud.apellidos}'.strip(),
        'programa': solicitud.nombre_curso,
        'periodo': solicitud.periodo_academico or 'No informado',
        'sede': solicitud.sede or 'No informada',
        'valor_plan': solicitud.valor_plan,
        'plazo_meses': solicitud.plazo_meses,
        'estado': _estado_publico(solicitud.estado),
        'seguimiento': SEGUIMIENTO_POR_ESTADO.get(
            solicitud.estado,
            'Revision adicional',
        ),
        'creada_en': solicitud.creada_en,
        'actualizada_en': solicitud.actualizada_en,
    }


def _puede_ver_contacto_completo(rol):
    return rol in {
        MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
        MembresiaInstitucion.Rol.INSTITUTION_ANALYST,
    }


def _presentar_documentos(solicitud):
    return [
        {
            'tipo': documento.get_tipo_display(),
            'escaneo': documento.get_estado_escaneo_display(),
            'validacion': documento.get_estado_validacion_display(),
            'requiere_correccion': documento.estado_validacion == 'REJECTED',
        }
        for documento in getattr(solicitud, 'documentos_dashboard', ())
    ]


def _presentar_linea_tiempo(solicitud):
    eventos = []
    ultimo_codigo = None
    for evento in getattr(solicitud, 'historial_dashboard', ()):
        estado = _estado_publico(evento.estado_nuevo)
        if estado['codigo'] == ultimo_codigo:
            continue
        eventos.append({
            'estado': estado,
            'fecha': evento.creado_en,
            'descripcion': SEGUIMIENTO_POR_ESTADO.get(
                evento.estado_nuevo,
                'Actualizacion de la solicitud',
            ),
        })
        ultimo_codigo = estado['codigo']
    estado_actual = _estado_publico(solicitud.estado)
    if not eventos or eventos[-1]['estado']['codigo'] != estado_actual['codigo']:
        eventos.append({
            'estado': estado_actual,
            'fecha': solicitud.actualizada_en,
            'descripcion': SEGUIMIENTO_POR_ESTADO.get(
                solicitud.estado,
                'Actualizacion de la solicitud',
            ),
        })
    return eventos


def _presentar_firma(solicitud):
    pagare = next(
        (
            artefacto
            for artefacto in getattr(solicitud, 'artefactos_dashboard', ())
            if artefacto.tipo == TipoArtefactoContractualEducativo.PROMISSORY_NOTE
            and artefacto.vigente
        ),
        None,
    )
    if not pagare:
        return {
            'documento': 'No generado',
            'firma': 'No iniciada',
            'firmado_en': None,
            'enviado_en': None,
            'rechazado_en': None,
        }
    proceso = getattr(pagare, 'proceso_firma', None)
    return {
        'documento': pagare.get_estado_display(),
        'firma': proceso.get_estado_display() if proceso else 'No iniciada',
        'firmado_en': proceso.firmado_en if proceso else None,
        'enviado_en': proceso.enviado_en if proceso else None,
        'rechazado_en': proceso.rechazado_en if proceso else None,
    }


def _presentar_finanzas(solicitud):
    fotografia = next(iter(getattr(solicitud, 'fotografias_dashboard', ())), None)
    if not fotografia:
        return {
            'disponible': False,
            'valor_solicitado': solicitud.valor_plan,
        }
    return {
        'disponible': True,
        'moneda': fotografia.moneda,
        'valor_solicitado': solicitud.valor_plan,
        'valor_financiado': fotografia.capital_financiado,
        'plazo_meses': fotografia.plazo_meses,
        'cuota_estimada': fotografia.valor_cuota_estimada,
        'total_estimado': fotografia.total_estimado,
    }


def presentar_detalle_solicitud(solicitud, *, rol):
    contacto_completo = _puede_ver_contacto_completo(rol)
    firma = _presentar_firma(solicitud)
    resultado_publico = obtener_resultado_publico(solicitud)
    return {
        **presentar_resumen_solicitud(solicitud),
        'correo': (
            solicitud.correo
            if contacto_completo
            else enmascarar_correo(solicitud.correo)
        ),
        'telefono': (
            solicitud.celular
            if contacto_completo
            else enmascarar_telefono(solicitud.celular)
        ),
        'documento': enmascarar_documento(
            solicitud.numero_documento_estudiante
        ),
        'tipo_documento': solicitud.get_tipo_documento_estudiante_display()
        if solicitud.tipo_documento_estudiante
        else 'No registrado',
        'codigo_matricula': solicitud.codigo_matricula or 'No informado',
        'jornada': solicitud.jornada or 'No informada',
        'fecha_matricula': solicitud.fecha_matricula,
        'documentos': _presentar_documentos(solicitud),
        'linea_tiempo': _presentar_linea_tiempo(solicitud),
        'firma': firma,
        'autorizacion_efectiva_en': (
            resultado_publico.autorizacion_efectiva_en
            if resultado_publico.curso_autorizado
            else None
        ),
        'finanzas': _presentar_finanzas(solicitud),
    }
