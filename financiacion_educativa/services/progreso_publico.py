from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse

from financiacion_educativa.choices import (
    EtapaAutomatizacionEducativa,
    EstadoProcesoAutomatizacionEducativa,
    EstadoSolicitudFinanciacion,
    RequisitoCorreccionEducativa,
    RolParticipante,
)
from financiacion_educativa.services.estado_publico import (
    obtener_resultado_publico,
)


PASOS_PROCESAMIENTO = (
    ('RECEIVED', 'Recibimos tus documentos'),
    ('SECURITY_SCAN', 'Verificando la seguridad de los archivos'),
    ('DOCUMENT_VALIDATION', 'Comprobando la calidad y la información'),
    ('DECISION', 'Validando el expediente'),
    ('FINANCIAL_SNAPSHOT', 'Preparando las condiciones de financiación'),
    ('CONTRACT_GENERATION', 'Generando los documentos contractuales'),
    ('SIGNATURE_SEND', 'Enviando el pagaré para firma'),
    ('WAITING_SIGNATURE', 'Pagaré pendiente de firma'),
)

ETAPAS_PUBLICAS = {
    EtapaAutomatizacionEducativa.SECURITY_SCAN: 'SEGURIDAD_DOCUMENTAL',
    EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION: 'VALIDACION_DOCUMENTAL',
    EtapaAutomatizacionEducativa.DECISION: 'DECISION_DOCUMENTAL',
    EtapaAutomatizacionEducativa.FINANCIAL_SNAPSHOT: 'CONDICIONES_FINANCIERAS',
    EtapaAutomatizacionEducativa.CONTRACT_GENERATION: 'DOCUMENTOS_CONTRACTUALES',
    EtapaAutomatizacionEducativa.SIGNATURE_SEND: 'ENVIO_A_FIRMA',
    EtapaAutomatizacionEducativa.WAITING_SIGNATURE: 'ESPERA_DE_FIRMA',
    EtapaAutomatizacionEducativa.COMPLETED: 'COMPLETADO',
}

MENSAJES_ETAPA = {
    EtapaAutomatizacionEducativa.SECURITY_SCAN: (
        'Estamos verificando la seguridad de los archivos.'
    ),
    EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION: (
        'Estamos comprobando la calidad y la información de los documentos.'
    ),
    EtapaAutomatizacionEducativa.DECISION: (
        'Estamos validando el expediente con la información disponible.'
    ),
    EtapaAutomatizacionEducativa.FINANCIAL_SNAPSHOT: (
        'Estamos preparando las condiciones de financiación.'
    ),
    EtapaAutomatizacionEducativa.CONTRACT_GENERATION: (
        'Estamos generando los documentos contractuales.'
    ),
    EtapaAutomatizacionEducativa.SIGNATURE_SEND: (
        'Estamos enviando el pagaré para firma.'
    ),
    EtapaAutomatizacionEducativa.WAITING_SIGNATURE: (
        'Tu pagaré está listo. Revisa tu correo para firmar.'
    ),
    EtapaAutomatizacionEducativa.COMPLETED: (
        'La firma fue confirmada y el curso quedó autorizado.'
    ),
}

MENSAJES_CORRECCION = {
    RequisitoCorreccionEducativa.STUDENT: (
        'Necesitamos que actualices los datos del estudiante.'
    ),
    RequisitoCorreccionEducativa.GUARDIAN: (
        'Necesitamos que actualices los datos del tutor.'
    ),
    RequisitoCorreccionEducativa.STUDENT_ID_FRONT: (
        'La fotografía del frente de la identificación del estudiante debe repetirse.'
    ),
    RequisitoCorreccionEducativa.STUDENT_ID_BACK: (
        'Necesitamos una nueva fotografía del reverso de la identificación del estudiante.'
    ),
    RequisitoCorreccionEducativa.GUARDIAN_ID_FRONT: (
        'La fotografía del frente de la identificación del tutor debe repetirse.'
    ),
    RequisitoCorreccionEducativa.GUARDIAN_ID_BACK: (
        'Necesitamos una nueva fotografía del reverso de la identificación del tutor.'
    ),
    RequisitoCorreccionEducativa.INCOME_CERTIFICATE: (
        'El soporte de ingresos o la certificacion bancaria debe cargarse nuevamente.'
    ),
    RequisitoCorreccionEducativa.ENROLLMENT_EVIDENCE: (
        'El soporte de matrícula debe cargarse nuevamente.'
    ),
}


@dataclass(frozen=True)
class AccionPublica:
    label: str
    url: str

    def como_dict(self):
        return {'label': self.label, 'url': self.url}


def _url(nombre, solicitud, **kwargs):
    parametros = {'solicitud_id': solicitud.pk, **kwargs}
    return reverse(f'financiacion_educativa_web:{nombre}', kwargs=parametros)


def _accion_requisito(solicitud, codigo):
    if codigo in {
        RequisitoCorreccionEducativa.STUDENT_ID_FRONT,
        RequisitoCorreccionEducativa.STUDENT_ID_BACK,
    }:
        return AccionPublica(
            'Repetir identificación del estudiante',
            _url('capturar-identidad', solicitud, persona='estudiante'),
        )
    if codigo in {
        RequisitoCorreccionEducativa.GUARDIAN_ID_FRONT,
        RequisitoCorreccionEducativa.GUARDIAN_ID_BACK,
    }:
        return AccionPublica(
            'Repetir identificación del tutor',
            _url('capturar-identidad', solicitud, persona='tutor'),
        )
    if codigo == RequisitoCorreccionEducativa.ENROLLMENT_EVIDENCE:
        return AccionPublica(
            'Actualizar soporte de matrícula',
            _url('matricula', solicitud),
        )
    if codigo == RequisitoCorreccionEducativa.INCOME_CERTIFICATE:
        asignacion = solicitud.roles_participantes.filter(
            rol=RolParticipante.PRINCIPAL_DEBTOR,
        ).first()
        destino = _url('documento-cargar', solicitud)
        if asignacion:
            destino = f'{destino}?{urlencode({"tipo": "INCOME_CERTIFICATE", "participante": asignacion.participante_id})}'
        return AccionPublica('Cargar soporte financiero', destino)
    if codigo in {
        RequisitoCorreccionEducativa.STUDENT,
        RequisitoCorreccionEducativa.GUARDIAN,
    }:
        tipo = (
            'estudiante'
            if codigo == RequisitoCorreccionEducativa.STUDENT
            else 'tutor'
        )
        rol = (
            RolParticipante.STUDENT
            if tipo == 'estudiante'
            else RolParticipante.GUARDIAN
        )
        asignacion = solicitud.roles_participantes.filter(rol=rol).first()
        if asignacion:
            destino = _url(
                'participante-editar',
                solicitud,
                participante_id=asignacion.participante_id,
            )
        else:
            destino = (
                f'{_url("participante-nuevo", solicitud)}?'
                f'{urlencode({"tipo": tipo})}'
            )
        return AccionPublica(f'Actualizar datos del {tipo}', destino)
    return AccionPublica(
        'Revisar expediente',
        _url('documentacion', solicitud),
    )


def _requisitos_correccion(solicitud, proceso):
    codigos = []
    if proceso and proceso.estado == EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED:
        codigos = proceso.requisitos_correccion or []
    if not codigos:
        decision = solicitud.decisiones_revision.order_by('-creada_en', '-id').first()
        if decision:
            codigos = decision.requisitos_pendientes or []
    permitidos = set(RequisitoCorreccionEducativa.values)
    resultado = []
    for codigo in dict.fromkeys(codigos):
        if codigo not in permitidos:
            continue
        accion = _accion_requisito(solicitud, codigo)
        resultado.append({
            'message': MENSAJES_CORRECCION[codigo],
            'action': accion.como_dict(),
        })
    return resultado


def _pasos(etapa_actual, estado):
    if estado == 'COMPLETED':
        return [
            {'key': clave, 'label': etiqueta, 'state': 'complete'}
            for clave, etiqueta in PASOS_PROCESAMIENTO
        ]
    claves = [clave for clave, _ in PASOS_PROCESAMIENTO]
    etapa = etapa_actual if etapa_actual in claves else 'RECEIVED'
    indice = claves.index(etapa)
    pasos = []
    for posicion, (clave, etiqueta) in enumerate(PASOS_PROCESAMIENTO):
        if posicion < indice:
            estado_paso = 'complete'
        elif posicion == indice:
            estado_paso = (
                'action'
                if estado in {'CORRECTION_REQUIRED', 'MANUAL_EXCEPTION', 'FAILED'}
                else 'current'
            )
        else:
            estado_paso = 'pending'
        pasos.append({'key': clave, 'label': etiqueta, 'state': estado_paso})
    return pasos


def _progreso_base(*, solicitud, proceso, estado, etapa, mensaje,
                   accion=None, debe_consultar=False, es_terminal=False,
                   correcciones=None, condiciones=None):
    actualizada_en = (
        proceso.actualizada_en if proceso else solicitud.actualizada_en
    )
    return {
        'status': estado,
        'public_stage': ETAPAS_PUBLICAS.get(etapa, etapa),
        'message': mensaje,
        'steps': _pasos(etapa, estado),
        'requires_correction': estado == 'CORRECTION_REQUIRED',
        'correction_requirements': correcciones or [],
        'can_resume': bool(accion),
        'action': accion.como_dict() if accion else None,
        'should_poll': debe_consultar,
        'is_terminal': es_terminal,
        'financial_terms': condiciones,
        'updated_at': actualizada_en.isoformat(),
    }


def obtener_progreso_publico(solicitud):
    proceso = solicitud.procesos_automatizacion.order_by(
        '-version_expediente'
    ).first()
    estado_solicitud = solicitud.estado

    if estado_solicitud == EstadoSolicitudFinanciacion.CORRECTION_REQUIRED:
        correcciones = _requisitos_correccion(solicitud, proceso)
        return _progreso_base(
            solicitud=solicitud,
            proceso=proceso,
            estado='CORRECTION_REQUIRED',
            etapa=(proceso.etapa_actual if proceso else 'CORRECCION_DOCUMENTAL'),
            mensaje=(
                'Necesitamos que actualices algunos elementos antes de continuar.'
            ),
            accion=AccionPublica(
                'Corregir expediente',
                _url('documentacion', solicitud),
            ),
            correcciones=correcciones,
        )

    if estado_solicitud == EstadoSolicitudFinanciacion.PENDING_SIGNATURE:
        return _progreso_base(
            solicitud=solicitud,
            proceso=proceso,
            estado='PENDING_SIGNATURE',
            etapa=EtapaAutomatizacionEducativa.WAITING_SIGNATURE,
            mensaje='Tu pagaré está listo. Revisa tu correo y la carpeta de spam para firmar.',
            accion=AccionPublica(
                'Consultar estado',
                _url('estado-solicitud', solicitud),
            ),
            debe_consultar=True,
        )

    if estado_solicitud == EstadoSolicitudFinanciacion.APPROVED:
        resultado = obtener_resultado_publico(solicitud)
        if resultado.curso_autorizado:
            return _progreso_base(
                solicitud=solicitud,
                proceso=proceso,
                estado='COMPLETED',
                etapa=EtapaAutomatizacionEducativa.COMPLETED,
                mensaje='La firma fue confirmada y el curso quedó autorizado.',
                accion=AccionPublica(
                    'Consultar financiación',
                    _url('finanzas', solicitud),
                ),
                es_terminal=True,
                condiciones=resultado.condiciones_financieras,
            )
        proceso = None

    if estado_solicitud in {
        EstadoSolicitudFinanciacion.ACTIVE,
        EstadoSolicitudFinanciacion.PAYMENT_REPORTED,
        EstadoSolicitudFinanciacion.PAYMENT_UNDER_REVIEW,
        EstadoSolicitudFinanciacion.PAID,
    }:
        return _progreso_base(
            solicitud=solicitud,
            proceso=None,
            estado='CLOSED',
            etapa='RESULTADO_FINAL',
            mensaje='Consulta el estado vigente de tu financiación.',
            accion=AccionPublica(
                'Consultar financiación',
                _url('finanzas', solicitud),
            ),
            es_terminal=True,
        )

    if estado_solicitud in {
        EstadoSolicitudFinanciacion.REJECTED,
        EstadoSolicitudFinanciacion.CANCELLED,
    }:
        return _progreso_base(
            solicitud=solicitud,
            proceso=None,
            estado='CLOSED',
            etapa='RESULTADO_FINAL',
            mensaje='Consulta el resultado vigente de tu solicitud.',
            accion=AccionPublica(
                'Consultar resultado',
                _url('estado-solicitud', solicitud),
            ),
            es_terminal=True,
        )

    estados_previos = {
        EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION: (
            'Consultar estado', 'estado-solicitud'
        ),
        EstadoSolicitudFinanciacion.PENDING_TERMS: (
            'Revisar términos', 'terminos'
        ),
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT: (
            'Continuar expediente', 'documentacion'
        ),
        EstadoSolicitudFinanciacion.PENDING_GUARDIAN: (
            'Registrar tutor', 'documentacion'
        ),
    }
    if estado_solicitud in estados_previos:
        etiqueta, ruta = estados_previos[estado_solicitud]
        return _progreso_base(
            solicitud=solicitud,
            proceso=None,
            estado='NOT_STARTED',
            etapa='RECEIVED',
            mensaje='Continúa el paso pendiente para enviar tu expediente.',
            accion=AccionPublica(etiqueta, _url(ruta, solicitud)),
        )

    if estado_solicitud in {
        EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
    } and proceso and proceso.estado in {
        EstadoProcesoAutomatizacionEducativa.QUEUED,
        EstadoProcesoAutomatizacionEducativa.RUNNING,
        EstadoProcesoAutomatizacionEducativa.RETRYING,
        EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        EstadoProcesoAutomatizacionEducativa.FAILED,
    }:
        estado = proceso.estado
        if estado == EstadoProcesoAutomatizacionEducativa.RETRYING:
            mensaje = 'Estamos intentando nuevamente. No necesitas reenviar los documentos.'
        elif estado == EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION:
            mensaje = (
                'Tu expediente requiere una verificación adicional. '
                'No vuelvas a enviar documentos salvo que te lo solicitemos.'
            )
        elif estado == EstadoProcesoAutomatizacionEducativa.FAILED:
            mensaje = (
                'No pudimos completar la verificación en este momento. '
                'Tu expediente permanece guardado de forma segura.'
            )
        else:
            mensaje = MENSAJES_ETAPA.get(
                proceso.etapa_actual,
                'Estamos procesando tu expediente.',
            )
        accion = None
        if estado in {
            EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            EstadoProcesoAutomatizacionEducativa.FAILED,
        }:
            accion = AccionPublica(
                'Consultar estado',
                _url('estado-solicitud', solicitud),
            )
        return _progreso_base(
            solicitud=solicitud,
            proceso=proceso,
            estado=estado,
            etapa=proceso.etapa_actual,
            mensaje=mensaje,
            accion=accion,
            debe_consultar=estado in {
                EstadoProcesoAutomatizacionEducativa.QUEUED,
                EstadoProcesoAutomatizacionEducativa.RUNNING,
                EstadoProcesoAutomatizacionEducativa.RETRYING,
            },
        )

    return _progreso_base(
        solicitud=solicitud,
        proceso=None,
        estado='MANUAL_EXCEPTION',
        etapa='REVISION_ADICIONAL',
        mensaje=(
            'Tu expediente requiere una verificación adicional. '
            'No vuelvas a enviar documentos salvo que te lo solicitemos.'
        ),
        accion=AccionPublica(
            'Consultar estado',
            _url('estado-solicitud', solicitud),
        ),
    )
