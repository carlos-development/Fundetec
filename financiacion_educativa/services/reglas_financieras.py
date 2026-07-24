from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from financiacion_educativa.choices import MetodoCalculoFinanciero
from financiacion_educativa.models import CondicionesFinancieras


DOS_DECIMALES = Decimal('0.01')
CIEN = Decimal('100')


def _decimal_configurado(nombre, default):
    try:
        return Decimal(str(getattr(settings, nombre, default)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({nombre: 'La configuracion debe ser decimal.'}) from exc


def _dinero(valor):
    return Decimal(valor).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ResultadoCondicionesFinancieras:
    valor_financiado: Decimal
    plazo_meses: int
    tasa_interes_mensual: Decimal
    tasa_comision: Decimal
    valor_comision: Decimal
    tasa_iva_comision: Decimal
    valor_iva_comision: Decimal
    capital_financiado: Decimal
    valor_cuota_estimada: Decimal
    interes_total_estimado: Decimal
    total_estimado: Decimal
    metodo_calculo: str
    version_regla: str
    moneda: str = 'COP'


def calcular_condiciones_financieras_vigentes(*, valor_plan, plazo_meses):
    valor_plan = _dinero(valor_plan)
    plazo_meses = int(plazo_meses)
    if valor_plan <= 0:
        raise ValidationError({'valor_plan': 'El valor del plan debe ser positivo.'})
    if plazo_meses <= 0:
        raise ValidationError({'plazo_meses': 'El plazo debe ser positivo.'})

    tasa_interes = _decimal_configurado(
        'FINANCIACION_EDUCATIVA_TASA_MENSUAL',
        '1.9',
    )
    tasa_comision = _decimal_configurado(
        'FINANCIACION_EDUCATIVA_COMISION_PORCENTAJE',
        '10',
    )
    tasa_iva = _decimal_configurado(
        'FINANCIACION_EDUCATIVA_IVA_COMISION_PORCENTAJE',
        '19',
    )
    if min(tasa_interes, tasa_comision, tasa_iva) < 0:
        raise ValidationError('Las tasas financieras no pueden ser negativas.')

    valor_comision = _dinero(valor_plan * tasa_comision / CIEN)
    valor_iva = _dinero(valor_comision * tasa_iva / CIEN)
    capital_financiado = _dinero(valor_plan + valor_comision + valor_iva)
    tasa_decimal = tasa_interes / CIEN

    if tasa_decimal > 0:
        factor = (
            tasa_decimal * (Decimal('1') + tasa_decimal) ** plazo_meses
        ) / (
            (Decimal('1') + tasa_decimal) ** plazo_meses - Decimal('1')
        )
        valor_cuota = _dinero(capital_financiado * factor)
    else:
        valor_cuota = _dinero(capital_financiado / Decimal(plazo_meses))

    total_estimado = _dinero(valor_cuota * Decimal(plazo_meses))
    interes_estimado = _dinero(max(Decimal('0'), total_estimado - capital_financiado))

    return ResultadoCondicionesFinancieras(
        valor_financiado=valor_plan,
        plazo_meses=plazo_meses,
        tasa_interes_mensual=tasa_interes,
        tasa_comision=tasa_comision,
        valor_comision=valor_comision,
        tasa_iva_comision=tasa_iva,
        valor_iva_comision=valor_iva,
        capital_financiado=capital_financiado,
        valor_cuota_estimada=valor_cuota,
        interes_total_estimado=interes_estimado,
        total_estimado=total_estimado,
        metodo_calculo=MetodoCalculoFinanciero.FRENCH_AMORTIZATION,
        version_regla=str(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_REGLA_VERSION',
                'aprobado-financiacion-v1',
            )
        ),
    )


@transaction.atomic
def crear_fotografia_condiciones_financieras(solicitud):
    if hasattr(solicitud, 'condiciones_financieras'):
        raise ValidationError('La solicitud ya tiene condiciones financieras.')

    resultado = calcular_condiciones_financieras_vigentes(
        valor_plan=solicitud.valor_plan,
        plazo_meses=solicitud.plazo_meses,
    )
    condiciones = CondicionesFinancieras(
        solicitud=solicitud,
        valor_financiado=resultado.valor_financiado,
        plazo_meses=resultado.plazo_meses,
        tasa_interes_mensual=resultado.tasa_interes_mensual,
        tasa_comision=resultado.tasa_comision,
        valor_comision=resultado.valor_comision,
        tasa_iva_comision=resultado.tasa_iva_comision,
        valor_iva_comision=resultado.valor_iva_comision,
        capital_financiado=resultado.capital_financiado,
        valor_cuota_estimada=resultado.valor_cuota_estimada,
        interes_total_estimado=resultado.interes_total_estimado,
        total_estimado=resultado.total_estimado,
        metodo_calculo=resultado.metodo_calculo,
        base_calculo={
            'valor_plan': format(resultado.valor_financiado, 'f'),
            'plazo_meses': resultado.plazo_meses,
            'tasa_interes_mensual': format(resultado.tasa_interes_mensual, 'f'),
            'tasa_comision': format(resultado.tasa_comision, 'f'),
            'tasa_iva_comision': format(resultado.tasa_iva_comision, 'f'),
        },
        version_regla=resultado.version_regla,
        moneda=resultado.moneda,
        fecha_primer_vencimiento=None,
        fecha_ultimo_vencimiento=None,
    )
    condiciones.full_clean()
    condiciones.save()
    return condiciones
