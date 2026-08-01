import re
import secrets
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from instituciones.models import CredencialAPIInstitucion


PATRON_PREFIJO_CREDENCIAL = re.compile(r'^[a-z0-9_-]+$')
LONGITUD_MAXIMA_PREFIJO = CredencialAPIInstitucion._meta.get_field(
    'prefijo_clave'
).max_length


@dataclass(frozen=True, repr=False)
class CredencialEmitida:
    credencial: CredencialAPIInstitucion
    token: str


def _generar_prefijo():
    return secrets.token_hex(8)


def _generar_secreto():
    return secrets.token_urlsafe(32)


def normalizar_prefijo_credencial_api(valor):
    if valor is None:
        return None

    prefijo = str(valor).strip().lower()
    if not prefijo:
        raise ValidationError({'prefijo': 'El prefijo no puede estar vacio.'})
    if len(prefijo) > LONGITUD_MAXIMA_PREFIJO:
        raise ValidationError({
            'prefijo': (
                f'El prefijo no puede superar {LONGITUD_MAXIMA_PREFIJO} caracteres.'
            ),
        })
    if not PATRON_PREFIJO_CREDENCIAL.fullmatch(prefijo):
        raise ValidationError({
            'prefijo': (
                'El prefijo solo admite letras minusculas, numeros, guion y '
                'guion bajo.'
            ),
        })
    return prefijo


@transaction.atomic
def crear_credencial_api(
    *,
    institucion,
    nombre,
    alcances=None,
    expira_en=None,
    prefijo=None,
):
    if not institucion or not institucion.pk:
        raise ValidationError({'institucion': 'La institucion es obligatoria.'})
    if not institucion.activa:
        raise ValidationError({'institucion': 'La institucion debe estar activa.'})

    prefijo_normalizado = normalizar_prefijo_credencial_api(prefijo)
    if prefijo_normalizado is None:
        prefijo_normalizado = _generar_prefijo()
    elif CredencialAPIInstitucion.objects.filter(
        prefijo_clave=prefijo_normalizado
    ).exists():
        raise ValidationError({'prefijo': 'El prefijo ya esta en uso.'})

    secreto = _generar_secreto()
    credencial = CredencialAPIInstitucion(
        institucion=institucion,
        nombre=(nombre or '').strip(),
        prefijo_clave=prefijo_normalizado,
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


@transaction.atomic
def revocar_credencial_api(*, credencial):
    credencial = CredencialAPIInstitucion.objects.select_for_update().get(
        pk=credencial.pk
    )
    if not credencial.activa:
        return False
    credencial.activa = False
    credencial.save(update_fields=['activa', 'actualizada_en'])
    return True
