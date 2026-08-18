from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from instituciones.models import Institucion, MembresiaInstitucion


def _validar_actor(actor):
    if (
        not actor
        or not getattr(actor, 'is_authenticated', False)
        or not getattr(actor, 'pk', None)
    ):
        raise ValidationError('La mutacion requiere un actor autenticado.')


def _validar_rol(rol):
    if rol not in MembresiaInstitucion.Rol.values:
        raise ValidationError({'rol': 'Selecciona un rol institucional valido.'})


@transaction.atomic
def crear_membresia(*, usuario, institucion, rol, actor, activa=True):
    _validar_actor(actor)
    _validar_rol(rol)
    if not usuario or not getattr(usuario, 'pk', None):
        raise ValidationError({'usuario': 'El usuario es obligatorio.'})
    if not institucion or not getattr(institucion, 'pk', None):
        raise ValidationError({'institucion': 'La institucion es obligatoria.'})

    institucion = Institucion.objects.select_for_update().get(pk=institucion.pk)
    if activa and not institucion.activa:
        raise ValidationError({
            'institucion': 'No puede activarse acceso a una institucion inactiva.',
        })

    existente = MembresiaInstitucion.objects.select_for_update().filter(
        usuario=usuario,
        institucion=institucion,
    ).first()
    if existente:
        if existente.rol == rol and existente.activa == activa:
            return existente
        raise ValidationError(
            'La membresia ya existe; usa la operacion explicita correspondiente.'
        )

    ahora = timezone.now()
    membresia = MembresiaInstitucion(
        usuario=usuario,
        institucion=institucion,
        rol=rol,
        activa=activa,
        creado_por=actor,
        invitado_en=ahora,
        activado_en=ahora if activa else None,
        desactivado_en=None,
    )
    membresia.full_clean()
    try:
        with transaction.atomic():
            membresia.save()
    except IntegrityError as error:
        existente = MembresiaInstitucion.objects.select_for_update().filter(
            usuario=usuario,
            institucion=institucion,
        ).first()
        if existente and existente.rol == rol and existente.activa == activa:
            return existente
        raise ValidationError('La membresia institucional ya existe.') from error
    return membresia


@transaction.atomic
def activar_membresia(*, membresia, actor):
    _validar_actor(actor)
    membresia = MembresiaInstitucion.objects.select_for_update().get(
        pk=membresia.pk
    )
    institucion = Institucion.objects.select_for_update().get(
        pk=membresia.institucion_id
    )
    if membresia.activa:
        return membresia
    if not institucion.activa:
        raise ValidationError({
            'institucion': 'No puede activarse acceso a una institucion inactiva.',
        })
    membresia.activa = True
    membresia.activado_en = timezone.now()
    membresia.desactivado_en = None
    membresia.full_clean()
    membresia.save(
        update_fields=[
            'activa',
            'activado_en',
            'desactivado_en',
            'actualizada_en',
        ]
    )
    return membresia


@transaction.atomic
def desactivar_membresia(*, membresia, actor):
    _validar_actor(actor)
    membresia = MembresiaInstitucion.objects.select_for_update().get(
        pk=membresia.pk
    )
    if not membresia.activa:
        return membresia
    membresia.activa = False
    membresia.desactivado_en = timezone.now()
    membresia.full_clean()
    membresia.save(
        update_fields=['activa', 'desactivado_en', 'actualizada_en']
    )
    return membresia


@transaction.atomic
def cambiar_rol_membresia(*, membresia, rol, actor):
    _validar_actor(actor)
    _validar_rol(rol)
    membresia = MembresiaInstitucion.objects.select_for_update().get(
        pk=membresia.pk
    )
    if membresia.rol == rol:
        return membresia
    membresia.rol = rol
    membresia.full_clean()
    membresia.save(update_fields=['rol', 'actualizada_en'])
    return membresia


def obtener_membresias_activas_usuario(*, usuario):
    if (
        not usuario
        or not getattr(usuario, 'is_authenticated', False)
        or not getattr(usuario, 'is_active', False)
    ):
        return MembresiaInstitucion.objects.none()
    return (
        MembresiaInstitucion.objects.filter(
            usuario=usuario,
            activa=True,
            institucion__activa=True,
        )
        .select_related('institucion')
        .order_by('institucion__nombre_comercial', 'institucion_id')
    )


@dataclass(frozen=True)
class ResolucionInstitucionActiva:
    membresias: tuple
    membresia: MembresiaInstitucion | None
    seleccion_invalida: bool = False

    @property
    def requiere_seleccion(self):
        return len(self.membresias) > 1 and self.membresia is None


def resolver_institucion_activa(*, usuario, membresia_id=None):
    membresias = tuple(obtener_membresias_activas_usuario(usuario=usuario))
    seleccionada = None
    seleccion_invalida = False
    if membresia_id:
        identificador = str(membresia_id)
        seleccionada = next(
            (
                membresia
                for membresia in membresias
                if str(membresia.pk) == identificador
            ),
            None,
        )
        seleccion_invalida = seleccionada is None
    if seleccionada is None and len(membresias) == 1:
        seleccionada = membresias[0]
    return ResolucionInstitucionActiva(
        membresias=membresias,
        membresia=seleccionada,
        seleccion_invalida=seleccion_invalida,
    )
