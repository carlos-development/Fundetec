from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import RolParticipante
from financiacion_educativa.models import (
    ParticipanteFinanciacion,
    RolParticipanteFinanciacion,
)


EDAD_MINIMA_ADULTO = 18


@dataclass(frozen=True)
class DatosParticipante:
    nombres: str
    apellidos: str
    tipo_documento: str
    numero_documento: str
    fecha_nacimiento: date | None = None
    fecha_nacimiento_confirmada: bool = False
    usuario: object | None = None


def calcular_edad(fecha_nacimiento, fecha_referencia=None):
    if not fecha_nacimiento:
        return None
    fecha_referencia = fecha_referencia or timezone.localdate()
    return (
        fecha_referencia.year
        - fecha_nacimiento.year
        - (
            (fecha_referencia.month, fecha_referencia.day)
            < (fecha_nacimiento.month, fecha_nacimiento.day)
        )
    )


def _validar_fecha_confirmada(datos, etiqueta):
    if not datos.fecha_nacimiento_confirmada or not datos.fecha_nacimiento:
        raise ValidationError({
            'fecha_nacimiento': f'La fecha de nacimiento de {etiqueta} debe estar confirmada.',
        })


def _crear_participante(solicitud, datos):
    participante = ParticipanteFinanciacion(
        solicitud=solicitud,
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        tipo_documento=datos.tipo_documento,
        numero_documento=datos.numero_documento,
        fecha_nacimiento=datos.fecha_nacimiento,
        fecha_nacimiento_confirmada=datos.fecha_nacimiento_confirmada,
        usuario=datos.usuario,
        responsable_contractual=False,
    )
    participante.full_clean()
    participante.save()
    return participante


def _asignar_rol(solicitud, participante, rol):
    asignacion = RolParticipanteFinanciacion(
        solicitud=solicitud,
        participante=participante,
        rol=rol,
    )
    asignacion.full_clean()
    asignacion.save()
    return asignacion


@transaction.atomic
def registrar_adulto_como_estudiante_y_deudor(*, solicitud, datos):
    _validar_fecha_confirmada(datos, 'el estudiante')
    if calcular_edad(datos.fecha_nacimiento) < EDAD_MINIMA_ADULTO:
        raise ValidationError({'fecha_nacimiento': 'El estudiante no es mayor de edad.'})

    participante = _crear_participante(solicitud, datos)
    _asignar_rol(solicitud, participante, RolParticipante.STUDENT)
    _asignar_rol(solicitud, participante, RolParticipante.PRINCIPAL_DEBTOR)
    participante.responsable_contractual = True
    participante.save(update_fields=['responsable_contractual', 'actualizado_en'])
    return participante


@transaction.atomic
def registrar_estudiante_menor_con_tutor(*, solicitud, estudiante, tutor):
    _validar_fecha_confirmada(estudiante, 'el estudiante')
    _validar_fecha_confirmada(tutor, 'el tutor')

    if calcular_edad(estudiante.fecha_nacimiento) >= EDAD_MINIMA_ADULTO:
        raise ValidationError({'estudiante': 'El estudiante no es menor de edad.'})
    if calcular_edad(tutor.fecha_nacimiento) < EDAD_MINIMA_ADULTO:
        raise ValidationError({'tutor': 'El tutor debe ser mayor de edad.'})
    if (
        estudiante.tipo_documento == tutor.tipo_documento
        and estudiante.numero_documento.strip().upper()
        == tutor.numero_documento.strip().upper()
    ):
        raise ValidationError({'tutor': 'El estudiante no puede ser su propio tutor.'})

    participante_estudiante = _crear_participante(solicitud, estudiante)
    _asignar_rol(solicitud, participante_estudiante, RolParticipante.STUDENT)

    participante_tutor = _crear_participante(solicitud, tutor)
    _asignar_rol(solicitud, participante_tutor, RolParticipante.GUARDIAN)
    _asignar_rol(solicitud, participante_tutor, RolParticipante.PRINCIPAL_DEBTOR)
    participante_tutor.responsable_contractual = True
    participante_tutor.save(update_fields=['responsable_contractual', 'actualizado_en'])
    return participante_estudiante, participante_tutor
