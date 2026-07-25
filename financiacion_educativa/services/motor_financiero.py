from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from django.core.exceptions import ValidationError


UNO = Decimal('1')
CIEN = Decimal('100')
TREINTA = Decimal('30')
PESO = Decimal('1')


def decimal_exacto(valor, *, campo='valor'):
    if isinstance(valor, float):
        raise ValidationError({campo: 'No se permiten valores float.'})
    try:
        return Decimal(valor)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({campo: 'El valor debe ser decimal.'}) from exc


def redondear_peso(valor):
    return decimal_exacto(valor).quantize(PESO, rounding=ROUND_HALF_UP)


def porcentaje_a_decimal(porcentaje):
    return decimal_exacto(porcentaje) / CIEN


@dataclass(frozen=True)
class ResultadoCargos:
    monto_solicitado: Decimal
    valor_originacion: Decimal
    valor_iva_originacion: Decimal
    valor_fondo_garantias: Decimal
    valor_seguro_vida: Decimal
    capital_total_financiado: Decimal


@dataclass(frozen=True)
class CuotaCalculada:
    numero: int
    fecha_vencimiento: date
    saldo_inicial: Decimal
    interes: Decimal
    capital: Decimal
    valor_cuota: Decimal
    saldo_final: Decimal


@dataclass(frozen=True)
class ResultadoPlanAmortizacion:
    cuota_informativa: Decimal
    cuotas: tuple[CuotaCalculada, ...]
    intereses_totales: Decimal
    total_proyectado: Decimal


def calcular_cargos(
    *,
    monto_solicitado,
    porcentaje_originacion,
    porcentaje_iva_originacion,
    porcentaje_fondo_garantias,
    porcentaje_seguro_vida,
):
    monto = redondear_peso(decimal_exacto(monto_solicitado, campo='monto_solicitado'))
    if monto <= 0:
        raise ValidationError({'monto_solicitado': 'El monto debe ser positivo.'})
    porcentajes = [
        decimal_exacto(porcentaje_originacion),
        decimal_exacto(porcentaje_iva_originacion),
        decimal_exacto(porcentaje_fondo_garantias),
        decimal_exacto(porcentaje_seguro_vida),
    ]
    if min(porcentajes) < 0:
        raise ValidationError('Los porcentajes no pueden ser negativos.')

    originacion = redondear_peso(monto * porcentaje_a_decimal(porcentajes[0]))
    iva = redondear_peso(originacion * porcentaje_a_decimal(porcentajes[1]))
    fondo = redondear_peso(monto * porcentaje_a_decimal(porcentajes[2]))
    seguro = redondear_peso(monto * porcentaje_a_decimal(porcentajes[3]))
    capital = monto + originacion + iva + fondo + seguro
    return ResultadoCargos(
        monto_solicitado=monto,
        valor_originacion=originacion,
        valor_iva_originacion=iva,
        valor_fondo_garantias=fondo,
        valor_seguro_vida=seguro,
        capital_total_financiado=capital,
    )


def calcular_cuota_fija(*, principal, tasa_mensual_porcentaje, plazo_meses):
    principal = decimal_exacto(principal, campo='principal')
    tasa = porcentaje_a_decimal(tasa_mensual_porcentaje)
    try:
        plazo = int(plazo_meses)
    except (TypeError, ValueError) as exc:
        raise ValidationError({'plazo_meses': 'El plazo debe ser entero.'}) from exc
    if principal <= 0:
        raise ValidationError({'principal': 'El principal debe ser positivo.'})
    if plazo <= 0:
        raise ValidationError({'plazo_meses': 'El plazo debe ser positivo.'})
    if tasa < 0:
        raise ValidationError({'tasa': 'La tasa no puede ser negativa.'})
    with localcontext() as contexto:
        contexto.prec = 40
        if tasa == 0:
            cuota = principal / Decimal(plazo)
        else:
            factor = (UNO + tasa) ** plazo
            divisor = factor - UNO
            if divisor == 0:
                raise ValidationError('No fue posible calcular la anualidad.')
            cuota = principal * tasa * factor / divisor
    return redondear_peso(cuota)


def sumar_meses(fecha_inicial, meses):
    if not isinstance(fecha_inicial, date):
        raise ValidationError({'fecha_inicio': 'La fecha inicial es obligatoria.'})
    indice = fecha_inicial.month - 1 + meses
    anio = fecha_inicial.year + indice // 12
    mes = indice % 12 + 1
    ultimo_origen = monthrange(fecha_inicial.year, fecha_inicial.month)[1]
    ultimo_destino = monthrange(anio, mes)[1]
    es_fin_de_mes = fecha_inicial.day == ultimo_origen
    dia = ultimo_destino if es_fin_de_mes else min(fecha_inicial.day, ultimo_destino)
    return date(anio, mes, dia)


def generar_plan_amortizacion(
    *,
    principal,
    tasa_mensual_porcentaje,
    plazo_meses,
    fecha_inicio,
):
    principal = redondear_peso(decimal_exacto(principal, campo='principal'))
    cuota_fija = calcular_cuota_fija(
        principal=principal,
        tasa_mensual_porcentaje=tasa_mensual_porcentaje,
        plazo_meses=plazo_meses,
    )
    tasa = porcentaje_a_decimal(tasa_mensual_porcentaje)
    plazo = int(plazo_meses)
    saldo = principal
    cuotas = []
    for numero in range(1, plazo + 1):
        saldo_inicial = saldo
        interes = redondear_peso(saldo_inicial * tasa)
        if numero == plazo:
            capital = saldo_inicial
            valor_cuota = capital + interes
        else:
            capital = cuota_fija - interes
            if capital <= 0:
                raise ValidationError('La cuota no amortiza capital.')
            capital = min(capital, saldo_inicial)
            valor_cuota = capital + interes
        saldo = saldo_inicial - capital
        cuotas.append(
            CuotaCalculada(
                numero=numero,
                fecha_vencimiento=sumar_meses(fecha_inicio, numero),
                saldo_inicial=saldo_inicial,
                interes=interes,
                capital=capital,
                valor_cuota=valor_cuota,
                saldo_final=saldo,
            )
        )
    intereses = sum((cuota.interes for cuota in cuotas), Decimal('0'))
    total = sum((cuota.valor_cuota for cuota in cuotas), Decimal('0'))
    return ResultadoPlanAmortizacion(
        cuota_informativa=cuota_fija,
        cuotas=tuple(cuotas),
        intereses_totales=intereses,
        total_proyectado=total,
    )


def calcular_interes_causado_diario_30(
    *,
    saldo_capital,
    tasa_mensual_porcentaje,
    fecha_ultimo_corte,
    fecha_efectiva,
    fecha_proximo_vencimiento,
):
    if fecha_efectiva < fecha_ultimo_corte:
        raise ValidationError(
            {'fecha_efectiva': 'La fecha no puede ser anterior al ultimo corte.'}
        )
    if fecha_efectiva >= fecha_proximo_vencimiento:
        dias = 30
    else:
        dias = min(30, max(0, (fecha_efectiva - fecha_ultimo_corte).days))
    interes = (
        decimal_exacto(saldo_capital)
        * porcentaje_a_decimal(tasa_mensual_porcentaje)
        * Decimal(dias)
        / TREINTA
    )
    return redondear_peso(interes), dias


def generar_plan_reduccion_plazo(
    *,
    principal,
    tasa_mensual_porcentaje,
    cuota_programada,
    primera_fecha_vencimiento,
    dias_primer_periodo=30,
    interes_pendiente_inicial=Decimal('0'),
):
    saldo = redondear_peso(principal)
    cuota_programada = redondear_peso(cuota_programada)
    tasa = porcentaje_a_decimal(tasa_mensual_porcentaje)
    if saldo < 0 or cuota_programada <= 0:
        raise ValidationError('Saldo y cuota no son validos.')
    if saldo == 0:
        return ResultadoPlanAmortizacion(
            cuota_informativa=cuota_programada,
            cuotas=(),
            intereses_totales=Decimal('0'),
            total_proyectado=Decimal('0'),
        )

    cuotas = []
    numero = 1
    while saldo > 0:
        if numero > 1200:
            raise ValidationError('La proyeccion excede el plazo permitido.')
        dias = dias_primer_periodo if numero == 1 else 30
        interes = redondear_peso(saldo * tasa * Decimal(dias) / TREINTA)
        if numero == 1:
            interes += redondear_peso(interes_pendiente_inicial)
        if cuota_programada <= interes:
            raise ValidationError('La cuota programada no amortiza el saldo.')
        saldo_inicial = saldo
        capital = min(cuota_programada - interes, saldo_inicial)
        valor_cuota = capital + interes
        saldo = saldo_inicial - capital
        fecha = (
            primera_fecha_vencimiento
            if numero == 1
            else sumar_meses(primera_fecha_vencimiento, numero - 1)
        )
        cuotas.append(
            CuotaCalculada(
                numero=numero,
                fecha_vencimiento=fecha,
                saldo_inicial=saldo_inicial,
                interes=interes,
                capital=capital,
                valor_cuota=valor_cuota,
                saldo_final=saldo,
            )
        )
        numero += 1
    return ResultadoPlanAmortizacion(
        cuota_informativa=cuota_programada,
        cuotas=tuple(cuotas),
        intereses_totales=sum(
            (cuota.interes for cuota in cuotas),
            Decimal('0'),
        ),
        total_proyectado=sum(
            (cuota.valor_cuota for cuota in cuotas),
            Decimal('0'),
        ),
    )
