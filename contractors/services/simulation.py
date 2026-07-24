from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


DOS_DECIMALES = Decimal('0.01')
CUATRO_DECIMALES = Decimal('0.0001')


class ErrorSimulacionContratista(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoSimulacionCreditoContratista:
    organizacion_id: int | None
    configuracion_producto_id: int | None
    tipo_producto: str
    monto_solicitado: Decimal
    plazo_meses: int
    tasa_mensual: Decimal
    comision: Decimal
    iva_comision: Decimal
    capital_financiado: Decimal
    cuota_mensual: Decimal
    total_a_pagar: Decimal
    interes_estimado: Decimal
    configuracion_portal_id: int | None = None

    def como_dict(self):
        return {
            'organizacion_id': self.organizacion_id,
            'configuracion_producto_id': self.configuracion_producto_id,
            'configuracion_portal_id': self.configuracion_portal_id,
            'tipo_producto': self.tipo_producto,
            'monto_solicitado': self.monto_solicitado,
            'plazo_meses': self.plazo_meses,
            'tasa_mensual': self.tasa_mensual,
            'comision': self.comision,
            'iva_comision': self.iva_comision,
            'capital_financiado': self.capital_financiado,
            'cuota_mensual': self.cuota_mensual,
            'total_a_pagar': self.total_a_pagar,
            'interes_estimado': self.interes_estimado,
        }

    def as_dict(self):
        return {
            'organization_id': self.organizacion_id,
            'product_config_id': self.configuracion_producto_id,
            'product_type': self.tipo_producto,
            'requested_amount': self.monto_solicitado,
            'term_months': self.plazo_meses,
            'monthly_rate': self.tasa_mensual,
            'commission_amount': self.comision,
            'vat_amount': self.iva_comision,
            'principal_financed': self.capital_financiado,
            'monthly_payment': self.cuota_mensual,
            'total_to_pay': self.total_a_pagar,
            'estimated_interest': self.interes_estimado,
        }

    @property
    def organization_id(self):
        return self.organizacion_id

    @property
    def product_config_id(self):
        return self.configuracion_producto_id

    @property
    def product_type(self):
        return self.tipo_producto

    @property
    def requested_amount(self):
        return self.monto_solicitado

    @property
    def term_months(self):
        return self.plazo_meses

    @property
    def monthly_rate(self):
        return self.tasa_mensual

    @property
    def commission_amount(self):
        return self.comision

    @property
    def vat_amount(self):
        return self.iva_comision

    @property
    def principal_financed(self):
        return self.capital_financiado

    @property
    def monthly_payment(self):
        return self.cuota_mensual

    @property
    def total_to_pay(self):
        return self.total_a_pagar

    @property
    def estimated_interest(self):
        return self.interes_estimado


def simular_credito_contratista(*, organizacion, configuracion_producto, monto, plazo_meses):
    if organizacion is None:
        raise ErrorSimulacionContratista('organizacion_requerida')
    if configuracion_producto is None:
        raise ErrorSimulacionContratista('configuracion_producto_requerida')
    if configuracion_producto.organization_id != organizacion.id:
        raise ErrorSimulacionContratista('configuracion_no_pertenece_a_organizacion')
    if not configuracion_producto.is_active or not organizacion.is_active:
        raise ErrorSimulacionContratista('organizacion_o_configuracion_inactiva')

    monto = _dinero(monto)
    plazo_meses = int(plazo_meses)
    _validar_limites(configuracion_producto=configuracion_producto, monto=monto, plazo_meses=plazo_meses)

    tasa_mensual = _tasa(configuracion_producto.monthly_rate)
    comision_porcentual = (
        monto * (_tasa(configuracion_producto.commission_rate) / Decimal('100'))
    ).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    comision_fija = _dinero(configuracion_producto.commission_amount)
    comision = (comision_porcentual + comision_fija).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    iva_comision = (comision * (_tasa(configuracion_producto.vat_rate) / Decimal('100'))).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    capital_financiado = (monto + comision + iva_comision).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    cuota_mensual = _calcular_cuota_mensual(
        capital_financiado=capital_financiado,
        tasa_mensual=tasa_mensual,
        plazo_meses=plazo_meses,
    )
    total_a_pagar = (cuota_mensual * plazo_meses).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    interes_estimado = max(Decimal('0.00'), total_a_pagar - capital_financiado).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )

    return ResultadoSimulacionCreditoContratista(
        organizacion_id=organizacion.id,
        configuracion_producto_id=configuracion_producto.id,
        tipo_producto=configuracion_producto.product_type,
        monto_solicitado=monto,
        plazo_meses=plazo_meses,
        tasa_mensual=tasa_mensual,
        comision=comision,
        iva_comision=iva_comision,
        capital_financiado=capital_financiado,
        cuota_mensual=cuota_mensual,
        total_a_pagar=total_a_pagar,
        interes_estimado=interes_estimado,
    )


def simular_credito_portal_contratistas(*, configuracion_portal, monto, plazo_meses):
    if configuracion_portal is None:
        raise ErrorSimulacionContratista('configuracion_portal_requerida')
    if not configuracion_portal.activo:
        raise ErrorSimulacionContratista('configuracion_portal_inactiva')

    monto = _dinero(monto)
    plazo_meses = int(plazo_meses)
    _validar_limites(configuracion_producto=configuracion_portal, monto=monto, plazo_meses=plazo_meses)

    tasa_mensual = _tasa(configuracion_portal.tasa_mensual)
    comision_porcentual = (
        monto * (_tasa(configuracion_portal.tasa_comision) / Decimal('100'))
    ).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    comision_fija = _dinero(configuracion_portal.comision_fija)
    comision = (comision_porcentual + comision_fija).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    iva_comision = (comision * (_tasa(configuracion_portal.tasa_iva) / Decimal('100'))).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )
    capital_financiado = (monto + comision + iva_comision).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    cuota_mensual = _calcular_cuota_mensual(
        capital_financiado=capital_financiado,
        tasa_mensual=tasa_mensual,
        plazo_meses=plazo_meses,
    )
    total_a_pagar = (cuota_mensual * plazo_meses).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    interes_estimado = max(Decimal('0.00'), total_a_pagar - capital_financiado).quantize(
        DOS_DECIMALES,
        rounding=ROUND_HALF_UP,
    )

    return ResultadoSimulacionCreditoContratista(
        organizacion_id=None,
        configuracion_producto_id=None,
        configuracion_portal_id=configuracion_portal.id,
        tipo_producto='credito_contratista',
        monto_solicitado=monto,
        plazo_meses=plazo_meses,
        tasa_mensual=tasa_mensual,
        comision=comision,
        iva_comision=iva_comision,
        capital_financiado=capital_financiado,
        cuota_mensual=cuota_mensual,
        total_a_pagar=total_a_pagar,
        interes_estimado=interes_estimado,
    )


def _validar_limites(*, configuracion_producto, monto, plazo_meses):
    if monto < configuracion_producto.min_amount:
        raise ErrorSimulacionContratista('monto_menor_al_minimo')
    if monto > configuracion_producto.max_amount:
        raise ErrorSimulacionContratista('monto_supera_maximo')
    if plazo_meses < configuracion_producto.min_term_months:
        raise ErrorSimulacionContratista('plazo_menor_al_minimo')
    if plazo_meses > configuracion_producto.max_term_months:
        raise ErrorSimulacionContratista('plazo_supera_maximo')


def _calcular_cuota_mensual(*, capital_financiado, tasa_mensual, plazo_meses):
    tasa = tasa_mensual / Decimal('100')
    if tasa <= 0:
        return (capital_financiado / plazo_meses).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)

    factor = (tasa * (Decimal('1') + tasa) ** plazo_meses) / (
        ((Decimal('1') + tasa) ** plazo_meses) - Decimal('1')
    )
    return (capital_financiado * factor).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def _dinero(value):
    return Decimal(str(value)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def _tasa(value):
    return Decimal(str(value)).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)


# Aliases temporales de compatibilidad.
MONEY = DOS_DECIMALES
RATE_PLACES = CUATRO_DECIMALES
ContractorSimulationError = ErrorSimulacionContratista
ContractorCreditSimulationResult = ResultadoSimulacionCreditoContratista


def simulate_contractor_credit(*, organization, product_config, amount, term_months):
    return simular_credito_contratista(
        organizacion=organization,
        configuracion_producto=product_config,
        monto=amount,
        plazo_meses=term_months,
    )
