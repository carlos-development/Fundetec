import secrets
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from instituciones.models import CredencialAPIInstitucion


@dataclass(frozen=True, repr=False)
class CredencialEmitida:
    credencial: CredencialAPIInstitucion
    token: str


def _generar_prefijo():
    return secrets.token_hex(8)


def _generar_secreto():
    return secrets.token_urlsafe(32)


@transaction.atomic
def crear_credencial_api(*, institucion, nombre, alcances=None, expira_en=None):
    if not institucion or not institucion.pk:
        raise ValidationError({'institucion': 'La institucion es obligatoria.'})
    if not institucion.activa:
        raise ValidationError({'institucion': 'La institucion debe estar activa.'})

    secreto = _generar_secreto()
    credencial = CredencialAPIInstitucion(
        institucion=institucion,
        nombre=(nombre or '').strip(),
        prefijo_clave=_generar_prefijo(),
        alcances=list(alcances or []),
        activa=True,
        expira_en=expira_en,
    )
    credencial.establecer_secreto(secreto)
    credencial.full_clean()
    credencial.save()
    return CredencialEmitida(
        credencial=credencial,
        token=f'{credencial.prefijo_clave}.{secreto}',
    )


@transaction.atomic
def rotar_credencial_api(*, credencial, expira_en=None):
    credencial = CredencialAPIInstitucion.objects.select_for_update().get(
        pk=credencial.pk
    )
    if not credencial.institucion.activa:
        raise ValidationError({'institucion': 'La institucion debe estar activa.'})

    secreto = _generar_secreto()
    credencial.establecer_secreto(secreto)
    credencial.expira_en = expira_en
    credencial.activa = True
    credencial.full_clean()
    credencial.save(
        update_fields=['secreto_hash', 'expira_en', 'activa', 'actualizada_en']
    )
    return CredencialEmitida(
        credencial=credencial,
        token=f'{credencial.prefijo_clave}.{secreto}',
    )


def revocar_credencial_api(*, credencial):
    CredencialAPIInstitucion.objects.filter(pk=credencial.pk).update(activa=False)
