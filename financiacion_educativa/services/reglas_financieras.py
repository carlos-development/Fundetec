import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from financiacion_educativa.models import (
    CondicionesFinancieras,
    CuotaAmortizacionEducativa,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.configuracion_financiera import (
    seleccionar_configuracion_vigente,
)
from financiacion_educativa.services.motor_financiero import (
    calcular_cargos,
    generar_plan_amortizacion,
)


@dataclass(frozen=True)
class ResultadoCondicionesFinancieras:
    monto_solicitado: Decimal
    plazo_meses: int
    valor_originacion: Decimal
    valor_iva_originacion: Decimal
    valor_fondo_garantias: Decimal
    valor_seguro_vida: Decimal
    capital_total_financiado: Decimal
    cuota_informativa: Decimal
    intereses_totales: Decimal
    total_proyectado: Decimal
    plan: tuple


def calcular_condiciones_financieras(
    *,
    monto_solicitado,
    plazo_meses,
    configuracion,
    fecha_inicio_plan,
):
    if not isinstance(fecha_inicio_plan, date):
        raise ValidationError({'fecha_inicio_plan': 'La fecha inicial es obligatoria.'})
    cargos = calcular_cargos(
        monto_solicitado=monto_solicitado,
        porcentaje_originacion=configuracion.porcentaje_originacion,
        porcentaje_iva_originacion=configuracion.porcentaje_iva_originacion,
        porcentaje_fondo_garantias=configuracion.porcentaje_fondo_garantias,
        porcentaje_seguro_vida=configuracion.porcentaje_seguro_vida,
    )
    plan = generar_plan_amortizacion(
        principal=cargos.capital_total_financiado,
        tasa_mensual_porcentaje=configuracion.tasa_interes_mensual,
        plazo_meses=plazo_meses,
        fecha_inicio=fecha_inicio_plan,
    )
    return ResultadoCondicionesFinancieras(
        monto_solicitado=cargos.monto_solicitado,
        plazo_meses=int(plazo_meses),
        valor_originacion=cargos.valor_originacion,
        valor_iva_originacion=cargos.valor_iva_originacion,
        valor_fondo_garantias=cargos.valor_fondo_garantias,
        valor_seguro_vida=cargos.valor_seguro_vida,
        capital_total_financiado=cargos.capital_total_financiado,
        cuota_informativa=plan.cuota_informativa,
        intereses_totales=plan.intereses_totales,
        total_proyectado=plan.total_proyectado,
        plan=plan.cuotas,
    )


def _huella_determinantes(*, solicitud, configuracion, fecha_inicio_plan):
    datos = {
        'solicitud_id': str(solicitud.pk),
        'monto_solicitado': format(solicitud.valor_plan, 'f'),
        'plazo_meses': solicitud.plazo_meses,
        'configuracion_id': str(configuracion.pk),
        'codigo': configuracion.codigo,
        'version': configuracion.version,
        'porcentaje_originacion': format(
            configuracion.porcentaje_originacion,
            'f',
        ),
        'porcentaje_iva_originacion': format(
            configuracion.porcentaje_iva_originacion,
            'f',
        ),
        'porcentaje_fondo_garantias': format(
            configuracion.porcentaje_fondo_garantias,
            'f',
        ),
        'proveedor_fondo_garantias': configuracion.proveedor_fondo_garantias,
        'porcentaje_seguro_vida': format(
            configuracion.porcentaje_seguro_vida,
            'f',
        ),
        'proveedor_seguro_vida': configuracion.proveedor_seguro_vida,
        'tasa_interes_mensual': format(
            configuracion.tasa_interes_mensual,
            'f',
        ),
        'moneda': configuracion.moneda,
        'metodo_calculo': configuracion.metodo_calculo,
        'politica_redondeo': configuracion.politica_redondeo,
        'politica_causacion': configuracion.politica_causacion,
        'fecha_inicio_plan': fecha_inicio_plan.isoformat(),
    }
    serializado = json.dumps(datos, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serializado.encode('utf-8')).hexdigest(), datos


@transaction.atomic
def crear_fotografia_condiciones_financieras(
    solicitud,
    *,
    fecha_inicio_plan,
    fecha_calculo=None,
    configuracion=None,
    actor=None,
    bloquear=False,
):
    if not isinstance(fecha_inicio_plan, date):
        raise ValidationError({'fecha_inicio_plan': 'La fecha inicial es obligatoria.'})
    instante = fecha_calculo or timezone.now()
    if not isinstance(instante, datetime):
        raise ValidationError({'fecha_calculo': 'La fecha de calculo debe incluir hora.'})
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    configuracion = configuracion or seleccionar_configuracion_vigente(
        fecha_aplicacion=timezone.localtime(instante).date()
    )
    huella, base_calculo = _huella_determinantes(
        solicitud=solicitud,
        configuracion=configuracion,
        fecha_inicio_plan=fecha_inicio_plan,
    )
    activa = CondicionesFinancieras.objects.select_for_update().filter(
        solicitud=solicitud,
        activa=True,
    ).first()
    if activa and activa.huella_determinantes == huella:
        return activa
    if activa and activa.bloqueada:
        raise ValidationError(
            'La fotografia activa esta bloqueada y no puede recalcularse.'
        )

    resultado = calcular_condiciones_financieras(
        monto_solicitado=solicitud.valor_plan,
        plazo_meses=solicitud.plazo_meses,
        configuracion=configuracion,
        fecha_inicio_plan=fecha_inicio_plan,
    )
    numero_version = (
        CondicionesFinancieras.objects.filter(solicitud=solicitud).aggregate(
            maximo=Max('numero_version')
        )['maximo']
        or 0
    ) + 1
    if activa:
        CondicionesFinancieras.objects.filter(pk=activa.pk).update(activa=False)

    fotografia = CondicionesFinancieras.objects.create(
        solicitud=solicitud,
        configuracion=configuracion,
        numero_version=numero_version,
        activa=True,
        bloqueada=bloquear,
        es_legado=False,
        valor_financiado=resultado.monto_solicitado,
        plazo_meses=resultado.plazo_meses,
        tasa_interes_mensual=configuracion.tasa_interes_mensual,
        tasa_comision=configuracion.porcentaje_originacion,
        valor_comision=resultado.valor_originacion,
        tasa_iva_comision=configuracion.porcentaje_iva_originacion,
        valor_iva_comision=resultado.valor_iva_originacion,
        tasa_fondo_garantias=configuracion.porcentaje_fondo_garantias,
        valor_fondo_garantias=resultado.valor_fondo_garantias,
        proveedor_fondo_garantias=configuracion.proveedor_fondo_garantias,
        tasa_seguro_vida=configuracion.porcentaje_seguro_vida,
        valor_seguro_vida=resultado.valor_seguro_vida,
        proveedor_seguro_vida=configuracion.proveedor_seguro_vida,
        capital_financiado=resultado.capital_total_financiado,
        valor_cuota_estimada=resultado.cuota_informativa,
        interes_total_estimado=resultado.intereses_totales,
        total_estimado=resultado.total_proyectado,
        metodo_calculo=configuracion.metodo_calculo,
        base_calculo=base_calculo,
        version_regla=f'{configuracion.codigo}-v{configuracion.version}',
        moneda=configuracion.moneda,
        politica_redondeo=configuracion.politica_redondeo,
        politica_causacion=configuracion.politica_causacion,
        fecha_calculo=instante,
        fecha_inicio_plan=fecha_inicio_plan,
        fecha_primer_vencimiento=resultado.plan[0].fecha_vencimiento,
        fecha_ultimo_vencimiento=resultado.plan[-1].fecha_vencimiento,
        huella_determinantes=huella,
        creada_por=actor,
    )
    CuotaAmortizacionEducativa.objects.bulk_create(
        [
            CuotaAmortizacionEducativa(
                fotografia=fotografia,
                numero=cuota.numero,
                fecha_vencimiento=cuota.fecha_vencimiento,
                saldo_inicial=cuota.saldo_inicial,
                interes=cuota.interes,
                capital=cuota.capital,
                valor_cuota=cuota.valor_cuota,
                saldo_final=cuota.saldo_final,
            )
            for cuota in resultado.plan
        ]
    )
    return fotografia
