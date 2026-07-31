import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.models import (
    RegistroIdempotenciaSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.solicitudes import (
    DatosSolicitudFinanciacion,
    crear_solicitud_financiacion,
)
from instituciones.models import Institucion


DOS_DECIMALES = Decimal('0.01')


class ConflictoIdempotencia(Exception):
    pass


class ConflictoReferenciaExterna(Exception):
    pass


@dataclass(frozen=True)
class ResultadoCreacionIdempotente:
    solicitud: SolicitudFinanciacionEducativa
    repetida: bool


def _dinero_canonico(valor):
    return format(
        Decimal(valor).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP),
        'f',
    )


def payload_canonico_desde_datos(datos):
    return {
        'external_reference': datos.referencia_externa.strip(),
        'first_names': datos.nombres.strip(),
        'last_names': datos.apellidos.strip(),
        'phone': datos.celular.strip(),
        'email': datos.correo.strip(),
        'address': datos.direccion.strip(),
        'document_type': datos.tipo_documento_estudiante.strip(),
        'document_number': datos.numero_documento_estudiante.strip(),
        'birth_date': (
            datos.fecha_nacimiento_estudiante.isoformat()
            if datos.fecha_nacimiento_estudiante
            else None
        ),
        'enrollment_code': datos.codigo_matricula.strip(),
        'academic_period': datos.periodo_academico.strip(),
        'campus': datos.sede.strip(),
        'schedule': datos.jornada.strip(),
        'enrollment_date': None,
        'plan_value': _dinero_canonico(datos.valor_plan),
        'term': int(datos.plazo_meses),
        'program_name': datos.nombre_curso.strip(),
    }


def payload_canonico_desde_solicitud(solicitud):
    return {
        'external_reference': solicitud.referencia_externa,
        'first_names': solicitud.nombres,
        'last_names': solicitud.apellidos,
        'phone': solicitud.celular,
        'email': solicitud.correo,
        'address': solicitud.direccion,
        'document_type': solicitud.tipo_documento_estudiante,
        'document_number': solicitud.numero_documento_estudiante,
        'birth_date': (
            solicitud.fecha_nacimiento_estudiante.isoformat()
            if solicitud.fecha_nacimiento_estudiante
            else None
        ),
        'enrollment_code': solicitud.codigo_matricula,
        'academic_period': solicitud.periodo_academico,
        'campus': solicitud.sede,
        'schedule': solicitud.jornada,
        'enrollment_date': (
            solicitud.fecha_matricula.isoformat()
            if solicitud.fecha_matricula
            else None
        ),
        'plan_value': _dinero_canonico(solicitud.valor_plan),
        'term': solicitud.plazo_meses,
        'program_name': solicitud.nombre_curso,
    }


def calcular_hash_payload(payload):
    serializado = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(serializado.encode('utf-8')).hexdigest()


def calcular_hash_clave_idempotencia(clave):
    return hashlib.sha256(clave.encode('utf-8')).hexdigest()


@transaction.atomic
def crear_solicitud_idempotente(*, institucion, clave_idempotencia, datos):
    if not clave_idempotencia or not clave_idempotencia.strip():
        raise ValidationError({
            'Idempotency-Key': 'El encabezado de idempotencia es obligatorio.',
        })
    if len(clave_idempotencia) > 255:
        raise ValidationError({
            'Idempotency-Key': 'La clave no puede superar 255 caracteres.',
        })
    if not isinstance(datos, DatosSolicitudFinanciacion):
        raise ValidationError({'datos': 'Los datos de la solicitud son invalidos.'})

    institucion = Institucion.objects.select_for_update().get(pk=institucion.pk)
    if not institucion.activa:
        raise ValidationError({'institucion': 'La institucion debe estar activa.'})

    clave_hash = calcular_hash_clave_idempotencia(clave_idempotencia.strip())
    payload_hash = calcular_hash_payload(payload_canonico_desde_datos(datos))
    registro = RegistroIdempotenciaSolicitud.objects.select_for_update().filter(
        institucion=institucion,
        clave_hash=clave_hash,
    ).select_related('solicitud').first()

    if registro:
        if registro.payload_hash != payload_hash:
            raise ConflictoIdempotencia()
        RegistroIdempotenciaSolicitud.objects.filter(pk=registro.pk).update(
            ultimo_reuso_en=timezone.now()
        )
        return ResultadoCreacionIdempotente(
            solicitud=registro.solicitud,
            repetida=True,
        )

    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().filter(
        institucion=institucion,
        referencia_externa=datos.referencia_externa.strip(),
    ).first()
    repetida = solicitud is not None
    if solicitud:
        hash_existente = calcular_hash_payload(
            payload_canonico_desde_solicitud(solicitud)
        )
        if hash_existente != payload_hash:
            raise ConflictoReferenciaExterna()
    else:
        solicitud = crear_solicitud_financiacion(
            institucion=institucion,
            datos=datos,
            usuario=None,
        )

    RegistroIdempotenciaSolicitud.objects.create(
        institucion=institucion,
        clave_hash=clave_hash,
        payload_hash=payload_hash,
        solicitud=solicitud,
        ultimo_reuso_en=timezone.now() if repetida else None,
    )
    return ResultadoCreacionIdempotente(
        solicitud=solicitud,
        repetida=repetida,
    )
