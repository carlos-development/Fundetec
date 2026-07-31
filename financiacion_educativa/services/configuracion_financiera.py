from datetime import date
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from financiacion_educativa.choices import EstadoConfiguracionFinanciera
from financiacion_educativa.models import ConfiguracionFinancieraEducativa


CODIGO_POLITICA_ESTANDAR = 'EDU_STANDARD'
logger = logging.getLogger(__name__)


class ConfiguracionFinancieraNoDisponible(ValidationError):
    pass


class ConfiguracionFinancieraAmbigua(ValidationError):
    pass


def seleccionar_configuracion_vigente(
    *,
    fecha_aplicacion,
    codigo=CODIGO_POLITICA_ESTANDAR,
):
    if not isinstance(fecha_aplicacion, date):
        raise ValidationError({'fecha_aplicacion': 'Indica una fecha de aplicacion.'})
    candidatas = list(
        ConfiguracionFinancieraEducativa.objects.filter(
            codigo=codigo,
            estado=EstadoConfiguracionFinanciera.ACTIVE,
            vigente_desde__lte=fecha_aplicacion,
        ).filter(
            Q(vigente_hasta__isnull=True)
            | Q(vigente_hasta__gte=fecha_aplicacion)
        ).order_by('-version')[:2]
    )
    if not candidatas:
        logger.warning(
            'Configuracion financiera no disponible: codigo=%s fecha=%s.',
            codigo,
            fecha_aplicacion.isoformat(),
        )
        raise ConfiguracionFinancieraNoDisponible(
            'No existe una configuracion financiera vigente.'
        )
    if len(candidatas) > 1:
        logger.error(
            'Configuracion financiera ambigua: codigo=%s fecha=%s.',
            codigo,
            fecha_aplicacion.isoformat(),
        )
        raise ConfiguracionFinancieraAmbigua(
            'Existen configuraciones financieras vigentes ambiguas.'
        )
    return candidatas[0]


@transaction.atomic
def activar_configuracion_financiera(*, configuracion, actor=None):
    configuracion = ConfiguracionFinancieraEducativa.objects.select_for_update().get(
        pk=configuracion.pk
    )
    if configuracion.estado == EstadoConfiguracionFinanciera.ACTIVE:
        return configuracion
    if configuracion.estado != EstadoConfiguracionFinanciera.DRAFT:
        raise ValidationError('Solo puede activarse una configuracion en borrador.')
    configuracion.full_clean()
    superpuestas = ConfiguracionFinancieraEducativa.objects.select_for_update().filter(
        codigo=configuracion.codigo,
        estado=EstadoConfiguracionFinanciera.ACTIVE,
        vigente_desde__lte=configuracion.vigente_hasta or date.max,
    ).filter(
        Q(vigente_hasta__isnull=True)
        | Q(vigente_hasta__gte=configuracion.vigente_desde)
    )
    if superpuestas.exists():
        raise ValidationError('La vigencia se superpone con una version activa.')
    ConfiguracionFinancieraEducativa.objects.filter(pk=configuracion.pk).update(
        estado=EstadoConfiguracionFinanciera.ACTIVE,
        actualizado_por=actor,
    )
    configuracion.refresh_from_db()
    return configuracion


@transaction.atomic
def retirar_configuracion_financiera(*, configuracion, actor=None):
    configuracion = ConfiguracionFinancieraEducativa.objects.select_for_update().get(
        pk=configuracion.pk
    )
    if configuracion.estado == EstadoConfiguracionFinanciera.RETIRED:
        return configuracion
    if configuracion.estado != EstadoConfiguracionFinanciera.ACTIVE:
        raise ValidationError('Solo puede retirarse una configuracion activa.')
    ConfiguracionFinancieraEducativa.objects.filter(pk=configuracion.pk).update(
        estado=EstadoConfiguracionFinanciera.RETIRED,
        actualizado_por=actor,
    )
    configuracion.refresh_from_db()
    return configuracion
