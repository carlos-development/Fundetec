from dataclasses import dataclass
from datetime import date
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    RelacionEstudiante,
    RolParticipante,
    TipoEventoParticipante,
)
from financiacion_educativa.models import (
    EventoParticipanteFinanciacion,
    ParticipanteFinanciacion,
    RolParticipanteFinanciacion,
    SolicitudFinanciacionEducativa,
)


@dataclass(frozen=True)
class DatosParticipante:
    nombres: str
    apellidos: str
    tipo_documento: str
    numero_documento: str
    fecha_nacimiento: date | None = None
    fecha_nacimiento_confirmada: bool = False
    correo: str = ''
    telefono: str = ''
    relacion_estudiante: str = ''
    pais_expedicion: str = ''
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


def fecha_referencia_solicitud(solicitud):
    if solicitud.creada_en:
        return timezone.localtime(solicitud.creada_en).date()
    return timezone.localdate()


def estudiante_requiere_tutor(estudiante, *, fecha_referencia=None):
    edad = calcular_edad(estudiante.fecha_nacimiento, fecha_referencia)
    return (
        edad is not None
        and edad < settings.FINANCIACION_EDUCATIVA_MAYORIA_EDAD
    )


def solicitud_requiere_tutor(solicitud, *, fecha_referencia=None):
    asignacion = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=RolParticipante.STUDENT).first()
    fecha_nacimiento = (
        asignacion.participante.fecha_nacimiento
        if asignacion
        else solicitud.fecha_nacimiento_estudiante
    )
    edad = calcular_edad(
        fecha_nacimiento,
        fecha_referencia or fecha_referencia_solicitud(solicitud),
    )
    return (
        edad is not None
        and edad < settings.FINANCIACION_EDUCATIVA_MAYORIA_EDAD
    )


def _validar_fecha_confirmada(datos, etiqueta):
    if not datos.fecha_nacimiento_confirmada or not datos.fecha_nacimiento:
        raise ValidationError({
            'fecha_nacimiento': (
                f'La fecha de nacimiento de {etiqueta} debe estar confirmada.'
            ),
        })


def _campos_datos(datos):
    return {
        'nombres': re.sub(r'\s+', ' ', (datos.nombres or '').strip()),
        'apellidos': re.sub(r'\s+', ' ', (datos.apellidos or '').strip()),
        'tipo_documento': datos.tipo_documento,
        'numero_documento': re.sub(
            r'[^A-Z0-9]',
            '',
            (datos.numero_documento or '').strip().upper(),
        ),
        'fecha_nacimiento': datos.fecha_nacimiento,
        'fecha_nacimiento_confirmada': datos.fecha_nacimiento_confirmada,
        'correo': (datos.correo or '').strip().lower(),
        'telefono': re.sub(r'\s+', '', (datos.telefono or '').strip()),
        'relacion_estudiante': datos.relacion_estudiante,
        'pais_expedicion': (datos.pais_expedicion or '').strip().upper(),
    }


def _validar_roles(roles):
    roles = set(roles)
    permitidos = set(RolParticipante.values)
    if not roles or not roles.issubset(permitidos):
        raise ValidationError({'roles': 'Selecciona al menos un rol valido.'})
    if {RolParticipante.STUDENT, RolParticipante.GUARDIAN}.issubset(roles):
        raise ValidationError({
            'roles': 'Una persona no puede declararse estudiante y tutor a la vez.',
        })
    return roles


def _crear_participante(solicitud, datos, actor=None):
    participante = ParticipanteFinanciacion(
        solicitud=solicitud,
        usuario=datos.usuario,
        creado_por=actor,
        actualizado_por=actor,
        responsable_contractual=False,
        **_campos_datos(datos),
    )
    participante.full_clean()
    participante.save()
    return participante


def _asignar_rol(solicitud, participante, rol, actor=None):
    asignacion = RolParticipanteFinanciacion(
        solicitud=solicitud,
        participante=participante,
        rol=rol,
        declarado_por=actor,
    )
    asignacion.full_clean()
    asignacion.save()
    return asignacion


def _validar_propiedad(solicitud, actor):
    if not actor or not actor.is_authenticated:
        raise ValidationError('Debes iniciar sesion para modificar participantes.')
    if not actor.is_staff and solicitud.usuario_id != actor.pk:
        raise ValidationError('La solicitud no esta disponible.')


@transaction.atomic
def registrar_o_actualizar_participante(
    *,
    solicitud,
    actor,
    datos,
    roles,
    participante_id=None,
):
    _validar_propiedad(solicitud, actor)
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado not in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
    }:
        raise ValidationError('La solicitud no admite cambios documentales.')

    roles = _validar_roles(roles)
    if RolParticipante.GUARDIAN in roles:
        if not solicitud_requiere_tutor(solicitud):
            raise ValidationError({
                'roles': 'Esta solicitud no requiere tutor.',
            })
        edad_tutor = calcular_edad(
            datos.fecha_nacimiento,
            fecha_referencia_solicitud(solicitud),
        )
        if (
            edad_tutor is None
            or edad_tutor < settings.FINANCIACION_EDUCATIVA_MAYORIA_EDAD
        ):
            raise ValidationError({
                'fecha_nacimiento': 'El tutor debe ser una persona adulta.',
            })
    candidato = ParticipanteFinanciacion(
        solicitud=solicitud,
        **_campos_datos(datos),
    )
    candidato.full_clean(
        exclude=['usuario', 'creado_por', 'actualizado_por'],
        validate_unique=False,
        validate_constraints=False,
    )
    documento_normalizado = candidato.numero_documento

    existentes = ParticipanteFinanciacion.objects.select_for_update().filter(
        solicitud=solicitud
    )
    if participante_id:
        participante = existentes.filter(pk=participante_id).first()
        if not participante:
            raise ValidationError('El participante no esta disponible.')
        duplicado = existentes.filter(
            tipo_documento=candidato.tipo_documento,
            numero_documento=documento_normalizado,
        ).exclude(pk=participante.pk)
        if duplicado.exists():
            raise ValidationError({'numero_documento': 'El participante ya existe.'})
        creado = False
    else:
        participante = existentes.filter(
            tipo_documento=candidato.tipo_documento,
            numero_documento=documento_normalizado,
        ).first()
        creado = participante is None
        if creado:
            participante = _crear_participante(solicitud, datos, actor=actor)

    campos_modificados = []
    if not creado:
        for campo, valor in _campos_datos(datos).items():
            valor_normalizado = getattr(candidato, campo)
            if getattr(participante, campo) != valor_normalizado:
                setattr(participante, campo, valor_normalizado)
                campos_modificados.append(campo)
        if campos_modificados:
            participante.actualizado_por = actor
            participante.full_clean()
            participante.save(
                update_fields=[
                    *campos_modificados,
                    'actualizado_por',
                    'actualizado_en',
                ]
            )

    conflictos = RolParticipanteFinanciacion.objects.select_for_update().filter(
        solicitud=solicitud,
        rol__in=roles,
    ).exclude(participante=participante)
    if conflictos.exists():
        raise ValidationError({
            'roles': 'Uno de los roles ya pertenece a otra persona.',
        })

    roles_actuales = set(participante.roles.values_list('rol', flat=True))
    if roles_actuales != roles:
        participante.roles.exclude(rol__in=roles).delete()
        for rol in roles - roles_actuales:
            _asignar_rol(solicitud, participante, rol, actor=actor)
        campos_modificados.append('roles')

    if creado or campos_modificados:
        EventoParticipanteFinanciacion.objects.create(
            participante=participante,
            tipo=(
                TipoEventoParticipante.CREATED
                if creado
                else TipoEventoParticipante.UPDATED
            ),
            actor=actor,
            campos_modificados=sorted(set(campos_modificados)),
        )
    return participante


def sincronizar_estudiante_desde_solicitud(*, solicitud, actor):
    if not solicitud.identidad_estudiante_completa:
        return None
    existente = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=RolParticipante.STUDENT).first()
    if existente:
        return existente.participante

    roles = {RolParticipante.STUDENT}
    if not solicitud_requiere_tutor(solicitud):
        roles.add(RolParticipante.PRINCIPAL_DEBTOR)
    return registrar_o_actualizar_participante(
        solicitud=solicitud,
        actor=actor,
        datos=DatosParticipante(
            nombres=solicitud.nombres,
            apellidos=solicitud.apellidos,
            tipo_documento=solicitud.tipo_documento_estudiante,
            numero_documento=solicitud.numero_documento_estudiante,
            fecha_nacimiento=solicitud.fecha_nacimiento_estudiante,
            fecha_nacimiento_confirmada=False,
            correo=solicitud.correo,
            telefono=solicitud.celular,
            relacion_estudiante=RelacionEstudiante.SELF,
            pais_expedicion='CO',
        ),
        roles=roles,
    )


@transaction.atomic
def registrar_adulto_como_estudiante_y_deudor(*, solicitud, datos):
    """Compatibility service retained for Phase 1 callers."""
    _validar_fecha_confirmada(datos, 'el estudiante')
    if calcular_edad(datos.fecha_nacimiento) < settings.FINANCIACION_EDUCATIVA_MAYORIA_EDAD:
        raise ValidationError({'fecha_nacimiento': 'El estudiante no es mayor de edad.'})

    participante = _crear_participante(solicitud, datos)
    _asignar_rol(solicitud, participante, RolParticipante.STUDENT)
    _asignar_rol(solicitud, participante, RolParticipante.PRINCIPAL_DEBTOR)
    participante.responsable_contractual = True
    participante.save(update_fields=['responsable_contractual', 'actualizado_en'])
    return participante


@transaction.atomic
def registrar_estudiante_menor_con_tutor(*, solicitud, estudiante, tutor):
    """Compatibility service retained for Phase 1 callers."""
    _validar_fecha_confirmada(estudiante, 'el estudiante')
    _validar_fecha_confirmada(tutor, 'el tutor')

    mayoria = settings.FINANCIACION_EDUCATIVA_MAYORIA_EDAD
    if calcular_edad(estudiante.fecha_nacimiento) >= mayoria:
        raise ValidationError({'estudiante': 'El estudiante no es menor de edad.'})
    if calcular_edad(tutor.fecha_nacimiento) < mayoria:
        raise ValidationError({'tutor': 'El tutor debe ser mayor de edad.'})
    if (
        estudiante.tipo_documento == tutor.tipo_documento
        and estudiante.numero_documento.strip().upper()
        == tutor.numero_documento.strip().upper()
    ):
        raise ValidationError({'tutor': 'El estudiante no puede ser su propio tutor.'})

    participante_estudiante = _crear_participante(solicitud, estudiante)
    _asignar_rol(solicitud, participante_estudiante, RolParticipante.STUDENT)

    if not tutor.relacion_estudiante:
        tutor = DatosParticipante(
            **{
                **tutor.__dict__,
                'relacion_estudiante': RelacionEstudiante.LEGAL_GUARDIAN,
            }
        )
    participante_tutor = _crear_participante(solicitud, tutor)
    _asignar_rol(solicitud, participante_tutor, RolParticipante.GUARDIAN)
    _asignar_rol(solicitud, participante_tutor, RolParticipante.PRINCIPAL_DEBTOR)
    participante_tutor.responsable_contractual = True
    participante_tutor.save(
        update_fields=['responsable_contractual', 'actualizado_en']
    )
    return participante_estudiante, participante_tutor
