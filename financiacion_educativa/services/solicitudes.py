from dataclasses import dataclass
from decimal import Decimal
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import (
    HistorialEstadoSolicitud,
    SolicitudFinanciacionEducativa,
)


@dataclass(frozen=True)
class DatosSolicitudFinanciacion:
    referencia_externa: str
    nombres: str
    apellidos: str
    celular: str
    correo: str
    direccion: str
    valor_plan: Decimal
    plazo_meses: int
    nombre_curso: str
    tipo_curso: str = ''
    tipo_documento_estudiante: str = ''
    numero_documento_estudiante: str = ''
    fecha_nacimiento_estudiante: date | None = None
    codigo_matricula: str = ''
    periodo_academico: str = ''
    sede: str = ''
    jornada: str = ''
    canal_origen: str = 'INSTITUTION_API'
    correlation_id: str = ''
    ip_origen: str | None = None
    user_agent_origen: str = ''


@transaction.atomic
def crear_solicitud_financiacion(*, institucion, datos, usuario=None):
    if not institucion:
        raise ValidationError({'institucion': 'La institucion es obligatoria.'})
    if not isinstance(datos, DatosSolicitudFinanciacion):
        raise ValidationError({'datos': 'Los datos de la solicitud son invalidos.'})

    solicitud = SolicitudFinanciacionEducativa(
        institucion=institucion,
        referencia_externa=datos.referencia_externa,
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        celular=datos.celular,
        correo=datos.correo,
        direccion=datos.direccion,
        tipo_documento_estudiante=datos.tipo_documento_estudiante,
        numero_documento_estudiante=datos.numero_documento_estudiante,
        fecha_nacimiento_estudiante=datos.fecha_nacimiento_estudiante,
        codigo_matricula=datos.codigo_matricula,
        periodo_academico=datos.periodo_academico,
        sede=datos.sede,
        jornada=datos.jornada,
        valor_plan=datos.valor_plan,
        plazo_meses=datos.plazo_meses,
        nombre_curso=datos.nombre_curso,
        tipo_curso=datos.tipo_curso,
        estado=EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
        usuario=usuario,
        canal_origen=datos.canal_origen,
        correlation_id=datos.correlation_id,
        ip_origen=datos.ip_origen,
        user_agent_origen=datos.user_agent_origen[:512],
    )
    solicitud.full_clean()
    solicitud.save()

    HistorialEstadoSolicitud.objects.create(
        solicitud=solicitud,
        estado_anterior=None,
        estado_nuevo=solicitud.estado,
        actor=None,
        motivo='Solicitud recibida desde la institucion originadora.',
        metadata={
            'canal_origen': solicitud.canal_origen,
            'correlation_id': solicitud.correlation_id,
        },
    )
    return solicitud
