from django.db import transaction

from financiacion_educativa.choices import TipoEventoSeguridadFinanciacion
from financiacion_educativa.models import EventoSeguridadFinanciacion


def normalizar_correo(correo):
    return (correo or '').strip().casefold()


def usuario_coincide_con_correo(usuario, correo):
    return bool(
        usuario
        and usuario.is_authenticated
        and normalizar_correo(usuario.email) == normalizar_correo(correo)
    )


def usuario_es_propietario_solicitud(usuario, solicitud):
    return bool(
        usuario
        and usuario.is_authenticated
        and solicitud.usuario_id == usuario.pk
        and usuario_coincide_con_correo(usuario, solicitud.correo)
    )


@transaction.atomic
def registrar_evento_seguridad(
    *,
    tipo,
    endpoint,
    solicitud=None,
    actor=None,
    metodo='',
):
    if tipo not in TipoEventoSeguridadFinanciacion.values:
        raise ValueError('Tipo de evento de seguridad no valido.')
    actor_seguro = (
        actor
        if actor is not None and getattr(actor, 'is_authenticated', False)
        else None
    )
    return EventoSeguridadFinanciacion.objects.create(
        solicitud=solicitud,
        actor=actor_seguro,
        tipo=tipo,
        endpoint=(endpoint or 'unknown')[:100],
        metadata={'method': (metodo or '').upper()[:10]},
    )
