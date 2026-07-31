from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError

from financiacion_educativa.choices import PoliticaCausacionInteres
from financiacion_educativa.services.motor_financiero import (
    calcular_interes_causado_diario_30,
    decimal_exacto,
    generar_plan_reduccion_plazo,
    redondear_peso,
    sumar_meses,
)


ADVERTENCIA_PROYECCION = (
    'Proyeccion informativa sin recaudo real. La causacion diaria base 30 '
    'requiere validacion contractual y contable antes de produccion.'
)


@dataclass(frozen=True)
class SaldoProyectado:
    saldo_capital: Decimal
    cuotas_cubiertas: int
    cuotas_pendientes: int
    fecha_ultimo_corte: date
    fecha_proximo_vencimiento: date | None
    fecha_final_programada: date | None
    intereses_futuros_programados: Decimal


@dataclass(frozen=True)
class ProyeccionAbonoCapital:
    saldo_antes_pago: Decimal
    intereses_causados: Decimal
    otros_conceptos_exigibles: Decimal
    valor_recibido: Decimal
    aplicado_intereses: Decimal
    aplicado_otros_conceptos: Decimal
    interes_pendiente: Decimal
    aplicado_capital: Decimal
    excedente: Decimal
    saldo_posterior: Decimal
    cuota_programada: Decimal
    cuotas_pendientes_antes: int
    nueva_cantidad_cuotas: int
    fecha_final_antes: date | None
    nueva_fecha_final: date | None
    ultima_cuota_estimada: Decimal
    intereses_futuros_antes: Decimal
    intereses_futuros_despues: Decimal
    nuevo_plan: tuple
    intereses_futuros_evitados: Decimal
    fecha_efectiva: date
    dias_causados: int
    politica_causacion: str
    participante_pagante_id: object | None
    advertencia: str = ADVERTENCIA_PROYECCION


@dataclass(frozen=True)
class ProyeccionPagoTotal:
    saldo_capital: Decimal
    intereses_causados: Decimal
    total_liquidacion: Decimal
    saldo_proyectado_posterior: Decimal
    intereses_futuros_excluidos: Decimal
    fecha_vigencia: date
    dias_causados: int
    politica_causacion: str
    participante_pagante_id: object | None
    advertencia: str = ADVERTENCIA_PROYECCION


def _validar_fotografia(fotografia):
    if fotografia.es_legado or not fotografia.configuracion_id:
        raise ValidationError(
            'La fotografia legada no admite proyecciones del motor nuevo.'
        )
    if fotografia.politica_causacion != PoliticaCausacionInteres.DAILY_30:
        raise ValidationError('La politica de causacion no esta implementada.')


def _validar_participante(fotografia, participante_id):
    if not participante_id:
        return None
    participante = fotografia.solicitud.participantes.filter(
        pk=participante_id
    ).first()
    if not participante:
        raise ValidationError({'participante': 'El participante no pertenece a la solicitud.'})
    return participante.pk


def calcular_saldo_proyectado(*, fotografia, cuotas_cubiertas=0):
    _validar_fotografia(fotografia)
    try:
        cubiertas = int(cuotas_cubiertas)
    except (TypeError, ValueError) as exc:
        raise ValidationError({'cuotas_cubiertas': 'El valor debe ser entero.'}) from exc
    cuotas = list(fotografia.cuotas.all().order_by('numero'))
    if cubiertas < 0 or cubiertas > len(cuotas):
        raise ValidationError({'cuotas_cubiertas': 'Cantidad fuera del plan.'})
    if cubiertas == 0:
        saldo = fotografia.capital_financiado
        ultimo_corte = fotografia.fecha_inicio_plan
    else:
        saldo = cuotas[cubiertas - 1].saldo_final
        ultimo_corte = cuotas[cubiertas - 1].fecha_vencimiento
    proxima = cuotas[cubiertas].fecha_vencimiento if cubiertas < len(cuotas) else None
    futuros = sum(
        (cuota.interes for cuota in cuotas[cubiertas:]),
        Decimal('0'),
    )
    return SaldoProyectado(
        saldo_capital=saldo,
        cuotas_cubiertas=cubiertas,
        cuotas_pendientes=len(cuotas[cubiertas:]),
        fecha_ultimo_corte=ultimo_corte,
        fecha_proximo_vencimiento=proxima,
        fecha_final_programada=(
            cuotas[-1].fecha_vencimiento if cubiertas < len(cuotas) else None
        ),
        intereses_futuros_programados=futuros,
    )


def _interes_a_fecha(*, fotografia, saldo, fecha_efectiva):
    if saldo.saldo_capital == 0:
        return Decimal('0'), 0
    if fecha_efectiva > saldo.fecha_proximo_vencimiento:
        raise ValidationError({
            'fecha_efectiva': (
                'La fecha supera el siguiente vencimiento; actualiza las '
                'cuotas hipoteticamente cubiertas.'
            ),
        })
    return calcular_interes_causado_diario_30(
        saldo_capital=saldo.saldo_capital,
        tasa_mensual_porcentaje=fotografia.tasa_interes_mensual,
        fecha_ultimo_corte=saldo.fecha_ultimo_corte,
        fecha_efectiva=fecha_efectiva,
        fecha_proximo_vencimiento=saldo.fecha_proximo_vencimiento,
    )


def proyectar_abono_capital(
    *,
    fotografia,
    valor_pago,
    fecha_efectiva,
    cuotas_cubiertas=0,
    participante_pagante_id=None,
):
    _validar_fotografia(fotografia)
    participante_id = _validar_participante(fotografia, participante_pagante_id)
    pago = redondear_peso(decimal_exacto(valor_pago, campo='valor_pago'))
    if pago <= 0:
        raise ValidationError({'valor_pago': 'El pago hipotetico debe ser positivo.'})
    saldo = calcular_saldo_proyectado(
        fotografia=fotografia,
        cuotas_cubiertas=cuotas_cubiertas,
    )
    if fecha_efectiva < saldo.fecha_ultimo_corte:
        raise ValidationError({
            'fecha_efectiva': 'La fecha no puede ser anterior al ultimo corte.',
        })
    if saldo.saldo_capital == 0:
        return ProyeccionAbonoCapital(
            saldo_antes_pago=Decimal('0'),
            intereses_causados=Decimal('0'),
            otros_conceptos_exigibles=Decimal('0'),
            valor_recibido=pago,
            aplicado_intereses=Decimal('0'),
            aplicado_otros_conceptos=Decimal('0'),
            interes_pendiente=Decimal('0'),
            aplicado_capital=Decimal('0'),
            excedente=pago,
            saldo_posterior=Decimal('0'),
            cuota_programada=fotografia.valor_cuota_estimada,
            cuotas_pendientes_antes=0,
            nueva_cantidad_cuotas=0,
            fecha_final_antes=None,
            nueva_fecha_final=None,
            ultima_cuota_estimada=Decimal('0'),
            intereses_futuros_antes=Decimal('0'),
            intereses_futuros_despues=Decimal('0'),
            nuevo_plan=(),
            intereses_futuros_evitados=Decimal('0'),
            fecha_efectiva=fecha_efectiva,
            dias_causados=0,
            politica_causacion=fotografia.politica_causacion,
            participante_pagante_id=participante_id,
        )

    interes_causado, dias = _interes_a_fecha(
        fotografia=fotografia,
        saldo=saldo,
        fecha_efectiva=fecha_efectiva,
    )
    otros_conceptos = Decimal('0')
    total_exigible_previo = interes_causado + otros_conceptos
    if pago <= total_exigible_previo:
        raise ValidationError({
            'valor_pago': (
                'El valor debe ser superior a los intereses y conceptos '
                'causados para producir un abono real a capital.'
            ),
        })
    aplicado_interes = interes_causado
    aplicado_otros = otros_conceptos
    interes_pendiente = Decimal('0')
    disponible_capital = max(Decimal('0'), pago - aplicado_interes)
    disponible_capital -= aplicado_otros
    aplicado_capital = min(disponible_capital, saldo.saldo_capital)
    excedente = max(
        Decimal('0'),
        disponible_capital - aplicado_capital,
    )
    saldo_posterior = saldo.saldo_capital - aplicado_capital

    if saldo_posterior == 0:
        nuevo_plan = ()
        intereses_proyectados = Decimal('0')
    else:
        if fecha_efectiva == saldo.fecha_proximo_vencimiento:
            primera_fecha = sumar_meses(saldo.fecha_proximo_vencimiento, 1)
            dias_restantes = 30
        else:
            primera_fecha = saldo.fecha_proximo_vencimiento
            dias_restantes = 30 - dias
        plan = generar_plan_reduccion_plazo(
            principal=saldo_posterior,
            tasa_mensual_porcentaje=fotografia.tasa_interes_mensual,
            cuota_programada=fotografia.valor_cuota_estimada,
            primera_fecha_vencimiento=primera_fecha,
            dias_primer_periodo=dias_restantes,
            interes_pendiente_inicial=interes_pendiente,
        )
        nuevo_plan = plan.cuotas
        intereses_proyectados = plan.intereses_totales

    interes_futuro_original = max(
        Decimal('0'),
        saldo.intereses_futuros_programados - interes_causado,
    )
    evitados = max(
        Decimal('0'),
        interes_futuro_original - intereses_proyectados,
    )
    nueva_fecha_final = (
        nuevo_plan[-1].fecha_vencimiento if nuevo_plan else fecha_efectiva
    )
    ultima_cuota = nuevo_plan[-1].valor_cuota if nuevo_plan else Decimal('0')
    return ProyeccionAbonoCapital(
        saldo_antes_pago=saldo.saldo_capital,
        intereses_causados=interes_causado,
        otros_conceptos_exigibles=otros_conceptos,
        valor_recibido=pago,
        aplicado_intereses=aplicado_interes,
        aplicado_otros_conceptos=aplicado_otros,
        interes_pendiente=interes_pendiente,
        aplicado_capital=aplicado_capital,
        excedente=excedente,
        saldo_posterior=saldo_posterior,
        cuota_programada=fotografia.valor_cuota_estimada,
        cuotas_pendientes_antes=saldo.cuotas_pendientes,
        nueva_cantidad_cuotas=len(nuevo_plan),
        fecha_final_antes=saldo.fecha_final_programada,
        nueva_fecha_final=nueva_fecha_final,
        ultima_cuota_estimada=ultima_cuota,
        intereses_futuros_antes=interes_futuro_original,
        intereses_futuros_despues=intereses_proyectados,
        nuevo_plan=nuevo_plan,
        intereses_futuros_evitados=evitados,
        fecha_efectiva=fecha_efectiva,
        dias_causados=dias,
        politica_causacion=fotografia.politica_causacion,
        participante_pagante_id=participante_id,
    )


def proyectar_pago_total(
    *,
    fotografia,
    fecha_efectiva,
    cuotas_cubiertas=0,
    participante_pagante_id=None,
):
    _validar_fotografia(fotografia)
    participante_id = _validar_participante(fotografia, participante_pagante_id)
    saldo = calcular_saldo_proyectado(
        fotografia=fotografia,
        cuotas_cubiertas=cuotas_cubiertas,
    )
    if fecha_efectiva < saldo.fecha_ultimo_corte:
        raise ValidationError({
            'fecha_efectiva': 'La fecha no puede ser anterior al ultimo corte.',
        })
    if saldo.saldo_capital:
        interes, dias = _interes_a_fecha(
            fotografia=fotografia,
            saldo=saldo,
            fecha_efectiva=fecha_efectiva,
        )
    else:
        interes, dias = Decimal('0'), 0
    return ProyeccionPagoTotal(
        saldo_capital=saldo.saldo_capital,
        intereses_causados=interes,
        total_liquidacion=saldo.saldo_capital + interes,
        saldo_proyectado_posterior=Decimal('0'),
        intereses_futuros_excluidos=max(
            Decimal('0'),
            saldo.intereses_futuros_programados - interes,
        ),
        fecha_vigencia=fecha_efectiva,
        dias_causados=dias,
        politica_causacion=fotografia.politica_causacion,
        participante_pagante_id=participante_id,
    )
