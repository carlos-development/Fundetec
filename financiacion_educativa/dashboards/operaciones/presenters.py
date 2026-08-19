import re

from financiacion_educativa.choices import (
    EstadoPublicoSolicitud,
    EtapaAutomatizacionEducativa,
    TipoArtefactoContractualEducativo,
)
from financiacion_educativa.services.estado_publico import MAPA_ESTADO_PUBLICO


CODIGO_CONTROLADO = re.compile(r'^[A-Z0-9_.-]{1,80}$')


def _codigo_controlado(valor, predeterminado='No informado'):
    texto = str(valor or '').strip()
    return texto if CODIGO_CONTROLADO.fullmatch(texto) else predeterminado


def _lista_codigos_controlados(valores):
    if not isinstance(valores, (list, tuple)):
        return []
    return [
        codigo
        for valor in valores[:20]
        if (codigo := _codigo_controlado(valor, ''))
    ]


def enmascarar_documento(valor):
    texto = ''.join(
        caracter for caracter in str(valor or '') if caracter.isalnum()
    )
    if not texto:
        return 'No registrado'
    visibles = min(4, len(texto))
    return f'{"*" * max(4, len(texto) - visibles)}{texto[-visibles:]}'


def enmascarar_correo(valor):
    texto = str(valor or '').strip()
    if '@' not in texto:
        return 'No registrado'
    local, dominio = texto.rsplit('@', 1)
    return f'{local[:1] or "*"}***@{dominio}'


def enmascarar_telefono(valor):
    texto = ''.join(
        caracter for caracter in str(valor or '') if caracter.isdigit()
    )
    if not texto:
        return 'No registrado'
    visibles = min(4, len(texto))
    return f'{"*" * max(4, len(texto) - visibles)}{texto[-visibles:]}'


def presentar_estado_publico(estado_interno):
    codigo = MAPA_ESTADO_PUBLICO.get(
        estado_interno,
        EstadoPublicoSolicitud.UNDER_REVIEW,
    )
    return {
        'codigo': codigo,
        'etiqueta': EstadoPublicoSolicitud(codigo).label,
        'clase': codigo.lower().replace('_', '-'),
    }


def presentar_etapa(codigo):
    if not codigo:
        return 'Sin proceso iniciado'
    try:
        return EtapaAutomatizacionEducativa(codigo).label
    except ValueError:
        return 'Etapa no disponible'


def presentar_resumen_solicitud(solicitud):
    return {
        'id': solicitud.pk,
        'referencia': solicitud.referencia_externa,
        'institucion': solicitud.institucion.nombre_comercial,
        'solicitante': f'{solicitud.nombres} {solicitud.apellidos}'.strip(),
        'documento': enmascarar_documento(
            solicitud.numero_documento_estudiante
        ),
        'programa': solicitud.nombre_curso,
        'periodo': solicitud.periodo_academico or 'No informado',
        'sede': solicitud.sede or 'No informada',
        'valor': solicitud.valor_plan,
        'estado': presentar_estado_publico(solicitud.estado),
        'etapa': presentar_etapa(
            getattr(solicitud, 'etapa_operativa', None)
        ),
        'excepcion': bool(
            getattr(solicitud, 'tiene_excepcion', False)
        ),
        'creada_en': solicitud.creada_en,
        'actualizada_en': solicitud.actualizada_en,
    }


def _presentar_participantes(solicitud, *, datos_integrales):
    participantes = []
    for participante in getattr(solicitud, 'participantes_operativos', ()):
        roles = [rol.get_rol_display() for rol in participante.roles.all()]
        participantes.append({
            'nombre': participante.nombre_completo,
            'roles': roles,
            'tipo_documento': participante.get_tipo_documento_display(),
            'documento': (
                participante.numero_documento
                if datos_integrales
                else enmascarar_documento(participante.numero_documento)
            ),
            'correo': (
                participante.correo
                if datos_integrales
                else enmascarar_correo(participante.correo)
            ),
            'telefono': (
                participante.telefono
                if datos_integrales
                else enmascarar_telefono(participante.telefono)
            ),
            'responsable_contractual': participante.responsable_contractual,
            'identidad_verificada': participante.identidad_verificada,
            'relacion_verificada': participante.relacion_verificada,
        })
    return participantes


def _presentar_validacion_ia(validacion):
    estructurado = validacion.resultado_estructurado
    politica = ''
    if isinstance(estructurado, dict):
        politica = _codigo_controlado(
            estructurado.get('policy_version'),
            '',
        )
    return {
        'numero': validacion.numero,
        'estado': validacion.get_estado_display(),
        'proveedor': validacion.proveedor or 'No informado',
        'modelo': validacion.modelo or 'No informado',
        'politica': politica or f'Esquema {validacion.version_esquema}',
        'confianza': validacion.confianza,
        'calidad': validacion.calidad,
        'legibilidad': validacion.legibilidad,
        'hallazgos': _lista_codigos_controlados(validacion.hallazgos),
        'codigo_error': _codigo_controlado(
            validacion.codigo_error,
            '',
        ),
        'iniciado_en': validacion.iniciado_en,
        'finalizado_en': validacion.finalizado_en,
    }


def _presentar_contenido(traza):
    return {
        'numero': traza.numero,
        'estado': traza.get_estado_display(),
        'clasificacion': (
            traza.get_clasificacion_display()
            if traza.clasificacion
            else 'No clasificado'
        ),
        'politica': _codigo_controlado(traza.version_politica),
        'razones': _lista_codigos_controlados(traza.codigos_razon),
        'iniciado_en': traza.iniciado_en,
        'finalizado_en': traza.finalizado_en,
    }


def _presentar_documentos(solicitud):
    documentos = []
    for documento in getattr(solicitud, 'documentos_operativos', ()):
        intentos = getattr(documento, 'intentos_operativos', ())
        validaciones = getattr(documento, 'validaciones_operativas', ())
        contenidos = getattr(documento, 'contenidos_operativos', ())
        ultimo_escaneo = intentos[0] if intentos else None
        documentos.append({
            'tipo': documento.get_tipo_display(),
            'escaneo': documento.get_estado_escaneo_display(),
            'validacion': documento.get_estado_validacion_display(),
            'cargado_en': documento.cargado_en,
            'ultimo_escaneo': ({
                'numero': ultimo_escaneo.numero,
                'estado': ultimo_escaneo.get_estado_display(),
                'proveedor': ultimo_escaneo.proveedor or 'No informado',
                'veredicto': _codigo_controlado(
                    ultimo_escaneo.veredicto,
                    'No informado',
                ),
                'codigo_error': _codigo_controlado(
                    ultimo_escaneo.codigo_error,
                    '',
                ),
                'iniciado_en': ultimo_escaneo.iniciado_en,
                'finalizado_en': ultimo_escaneo.finalizado_en,
            } if ultimo_escaneo else None),
            'validacion_ia': (
                _presentar_validacion_ia(validaciones[0])
                if validaciones
                else None
            ),
            'contenido': (
                _presentar_contenido(contenidos[0])
                if contenidos
                else None
            ),
        })
    return documentos


def _presentar_finanzas(solicitud):
    fotografia = next(
        iter(getattr(solicitud, 'fotografias_operativas', ())),
        None,
    )
    if not fotografia:
        return {
            'disponible': False,
            'valor_solicitado': solicitud.valor_plan,
        }
    return {
        'disponible': True,
        'valor_solicitado': solicitud.valor_plan,
        'capital_financiado': fotografia.capital_financiado,
        'cuota': fotografia.valor_cuota_estimada,
        'total': fotografia.total_estimado,
        'plazo': fotografia.plazo_meses,
        'moneda': fotografia.moneda,
        'version': fotografia.numero_version,
        'bloqueada': fotografia.bloqueada,
    }


def _presentar_firmas(solicitud):
    firmas = []
    for artefacto in getattr(solicitud, 'artefactos_operativos', ()):
        if artefacto.tipo != TipoArtefactoContractualEducativo.PROMISSORY_NOTE:
            continue
        proceso = getattr(artefacto, 'proceso_firma', None)
        firmas.append({
            'version': artefacto.numero_version,
            'vigente': artefacto.vigente,
            'estado_contractual': artefacto.get_estado_display(),
            'estado_firma': (
                proceso.get_estado_display() if proceso else 'No iniciada'
            ),
            'proveedor': proceso.proveedor if proceso else 'No informado',
            'intentos': proceso.intentos_envio if proceso else 0,
            'codigo_error': (
                _codigo_controlado(proceso.codigo_ultimo_error, '')
                if proceso else ''
            ),
            'enviado_en': proceso.enviado_en if proceso else None,
            'firmado_en': proceso.firmado_en if proceso else None,
            'rechazado_en': proceso.rechazado_en if proceso else None,
        })
    return firmas


def _presentar_procesos(solicitud):
    return [
        {
            'version_expediente': proceso.version_expediente,
            'estado': proceso.get_estado_display(),
            'etapa': proceso.get_etapa_actual_display(),
            'intento': proceso.intento_actual,
            'maximo_intentos': proceso.maximo_intentos,
            'codigo_razon': _codigo_controlado(proceso.codigo_razon, ''),
            'creada_en': proceso.creada_en,
            'actualizada_en': proceso.actualizada_en,
            'finalizada_en': proceso.finalizada_en,
            'etapas': [
                {
                    'etapa': etapa.get_etapa_display(),
                    'estado': etapa.get_estado_display(),
                    'intento': etapa.intento,
                    'codigo_razon': _codigo_controlado(
                        etapa.codigo_razon,
                        '',
                    ),
                    'iniciada_en': etapa.iniciada_en,
                    'finalizada_en': etapa.finalizada_en,
                }
                for etapa in getattr(proceso, 'etapas_operativas', ())
            ],
        }
        for proceso in getattr(solicitud, 'procesos_operativos', ())
    ]


def _presentar_outbox(solicitud):
    return [
        {
            'evento': correo.get_tipo_evento_display(),
            'mensaje': correo.get_codigo_mensaje_display(),
            'estado': correo.get_estado_display(),
            'intentos': correo.intentos,
            'maximo_intentos': correo.maximo_intentos,
            'codigo_error': _codigo_controlado(
                correo.codigo_ultimo_error,
                '',
            ),
            'creada_en': correo.creada_en,
            'actualizada_en': correo.actualizada_en,
            'enviada_en': correo.enviada_en,
        }
        for correo in getattr(solicitud, 'outbox_operativo', ())
    ]


def _presentar_decisiones(solicitud):
    return [
        {
            'tipo': decision.get_tipo_display(),
            'motivo': decision.get_motivo_display(),
            'requisitos': _lista_codigos_controlados(
                decision.requisitos_pendientes
            ),
            'responsable': decision.responsable.get_username(),
            'creada_en': decision.creada_en,
        }
        for decision in getattr(solicitud, 'decisiones_operativas', ())
    ]


def _presentar_linea_tiempo(solicitud):
    eventos = [
        {
            'tipo': 'Solicitud',
            'titulo': presentar_estado_publico(evento.estado_nuevo)['etiqueta'],
            'detalle': 'Cambio de estado de la solicitud',
            'fecha': evento.creado_en,
        }
        for evento in getattr(solicitud, 'historial_operativo', ())
    ]
    for proceso in getattr(solicitud, 'procesos_operativos', ()):
        for etapa in getattr(proceso, 'etapas_operativas', ()):
            eventos.append({
                'tipo': 'Automatizacion',
                'titulo': etapa.get_etapa_display(),
                'detalle': etapa.get_estado_display(),
                'fecha': etapa.finalizada_en or etapa.iniciada_en,
            })
    return sorted(eventos, key=lambda evento: evento['fecha'])


def presentar_detalle_solicitud(
    solicitud,
    *,
    datos_integrales,
    puede_ver_documentos,
    puede_ver_procesos,
):
    return {
        **presentar_resumen_solicitud(solicitud),
        'correo': (
            solicitud.correo
            if datos_integrales
            else enmascarar_correo(solicitud.correo)
        ),
        'telefono': (
            solicitud.celular
            if datos_integrales
            else enmascarar_telefono(solicitud.celular)
        ),
        'documento': (
            solicitud.numero_documento_estudiante or 'No registrado'
            if datos_integrales
            else enmascarar_documento(
                solicitud.numero_documento_estudiante
            )
        ),
        'direccion': (
            solicitud.direccion if datos_integrales else 'Acceso restringido'
        ),
        'fecha_nacimiento': (
            solicitud.fecha_nacimiento_estudiante
            if datos_integrales
            else None
        ),
        'codigo_matricula': solicitud.codigo_matricula or 'No informado',
        'jornada': solicitud.jornada or 'No informada',
        'fecha_matricula': solicitud.fecha_matricula,
        'participantes': _presentar_participantes(
            solicitud,
            datos_integrales=datos_integrales,
        ),
        'finanzas': _presentar_finanzas(solicitud),
        'firmas': _presentar_firmas(solicitud) if puede_ver_procesos else [],
        'documentos': (
            _presentar_documentos(solicitud) if puede_ver_documentos else []
        ),
        'procesos': (
            _presentar_procesos(solicitud) if puede_ver_procesos else []
        ),
        'outbox': _presentar_outbox(solicitud) if puede_ver_procesos else [],
        'decisiones': _presentar_decisiones(solicitud),
        'linea_tiempo': _presentar_linea_tiempo(solicitud),
        'puede_ver_documentos': puede_ver_documentos,
        'puede_ver_procesos': puede_ver_procesos,
        'datos_integrales': datos_integrales,
    }


CAMPOS_ESTRUCTURADOS_DOCUMENTALES = (
    'decision',
    'schema_version',
    'policy_version',
    'is_identity_document',
    'is_colombian_document',
    'visible_document_type',
    'required_fields_visible',
    'side_matches',
    'data_consistent',
    'physical_capture',
    'appears_real',
    'visual_integrity',
    'visible_tampering_signals',
    'tampering_confidence',
    'type_confidence',
    'data_confidence',
    'physical_capture_confidence',
)


def presentar_documento_revision(documento):
    validaciones = getattr(documento, 'validaciones_operativas', ())
    vigente = validaciones[0] if validaciones else None
    estructurado = {}
    if vigente and isinstance(vigente.resultado_estructurado, dict):
        estructurado = {
            campo: vigente.resultado_estructurado.get(campo)
            for campo in CAMPOS_ESTRUCTURADOS_DOCUMENTALES
            if campo in vigente.resultado_estructurado
        }
    decisiones = [
        {
            'id': decision.pk,
            'accion': decision.get_accion_display(),
            'motivo': (
                decision.get_motivo_display() if decision.motivo else ''
            ),
            'observacion_publica': decision.observacion_publica,
            'nota_interna': decision.nota_interna,
            'actor': decision.actor.get_username(),
            'anterior': decision.get_estado_documento_anterior_display(),
            'posterior': decision.get_estado_documento_posterior_display(),
            'creada_en': decision.creada_en,
        }
        for decision in getattr(
            documento,
            'decisiones_documentales_operativas',
            (),
        )
    ]
    return {
        'id': documento.pk,
        'solicitud_id': documento.solicitud_id,
        'referencia': documento.solicitud.referencia_externa,
        'institucion': documento.solicitud.institucion.nombre_comercial,
        'solicitante': (
            f'{documento.solicitud.nombres} '
            f'{documento.solicitud.apellidos}'
        ).strip(),
        'titular': (
            documento.participante.nombre_completo
            if documento.participante else 'Solicitante principal'
        ),
        'tipo': documento.get_tipo_display(),
        'content_type': documento.content_type,
        'escaneo': documento.get_estado_escaneo_display(),
        'validacion': documento.get_estado_validacion_display(),
        'cargado_en': documento.cargado_en,
        'vigente': _presentar_validacion_ia(vigente) if vigente else None,
        'dimensiones': ({
            'correspondencia_tipo': vigente.corresponde_tipo,
            'consistencia_datos': vigente.datos_consistentes,
            'indicios_imagen_real': vigente.indicios_imagen_real,
            'confianza_correspondencia_tipo': estructurado.get('type_confidence'),
            'confianza_consistencia_datos': estructurado.get('data_confidence'),
            'confianza_captura_fisica': estructurado.get('physical_capture_confidence'),
            'confianza_manipulacion': estructurado.get('tampering_confidence'),
        } if vigente else {}),
        'estructurado': estructurado,
        'historico_ia': [
            _presentar_validacion_ia(item) for item in validaciones[1:]
        ],
        'decisiones': decisiones,
    }


def presentar_resumen_documento_revision(documento):
    hallazgos = getattr(documento, 'validacion_hallazgos', None) or []
    return {
        'id': documento.pk,
        'institucion': documento.solicitud.institucion.nombre_comercial,
        'referencia': documento.solicitud.referencia_externa,
        'solicitante': (
            f'{documento.solicitud.nombres} '
            f'{documento.solicitud.apellidos}'
        ).strip(),
        'tipo': documento.get_tipo_display(),
        'escaneo': documento.get_estado_escaneo_display(),
        'validacion': documento.get_estado_validacion_display(),
        'confianza': getattr(documento, 'validacion_confianza', None),
        'hallazgos': _lista_codigos_controlados(hallazgos),
        'cargado_en': documento.cargado_en,
    }
