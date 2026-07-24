from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from financiacion_educativa.services.reglas_financieras import (
    calcular_condiciones_financieras_vigentes,
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import crear_solicitud
from gestion_creditos.services.credit_simulation import (
    PRODUCT_PAYROLL_LOAN,
    calculate_credit_simulation,
)


CONFIGURACION_CARACTERIZACION = {
    'LIBRANZA_TASA_MENSUAL': '1.9',
    'LIBRANZA_ORIGINATION_RATE': '10',
    'LIBRANZA_VAT_RATE': '19',
    'FINANCIACION_EDUCATIVA_TASA_MENSUAL': '1.9',
    'FINANCIACION_EDUCATIVA_COMISION_PORCENTAJE': '10',
    'FINANCIACION_EDUCATIVA_IVA_COMISION_PORCENTAJE': '19',
    'FINANCIACION_EDUCATIVA_REGLA_VERSION': 'caracterizacion-v1',
}


class ReglasFinancierasEducativasTests(TestCase):
    @override_settings(**CONFIGURACION_CARACTERIZACION)
    def test_resultado_caracteriza_regla_financiera_vigente(self):
        actual = calculate_credit_simulation(
            product_type=PRODUCT_PAYROLL_LOAN,
            amount=Decimal('1000000.00'),
            term_months=12,
            document_number='10001234',
        )
        educativo = calcular_condiciones_financieras_vigentes(
            valor_plan=Decimal('1000000.00'),
            plazo_meses=12,
        )

        self.assertEqual(educativo.valor_comision, Decimal(actual['origination_fee']))
        self.assertEqual(educativo.valor_iva_comision, Decimal(actual['vat']))
        self.assertEqual(educativo.valor_cuota_estimada, Decimal(actual['monthly_payment']))
        self.assertEqual(educativo.interes_total_estimado, Decimal(actual['interest']))
        self.assertEqual(educativo.total_estimado, Decimal(actual['total_to_pay']))

    @override_settings(**CONFIGURACION_CARACTERIZACION)
    def test_crea_fotografia_financiera_inmutable_sin_inventar_vencimiento(self):
        solicitud = crear_solicitud()
        condiciones = crear_fotografia_condiciones_financieras(solicitud)

        self.assertEqual(condiciones.valor_financiado, solicitud.valor_plan)
        self.assertEqual(condiciones.plazo_meses, solicitud.plazo_meses)
        self.assertEqual(condiciones.version_regla, 'caracterizacion-v1')
        self.assertIsNone(condiciones.fecha_primer_vencimiento)
        self.assertIsNone(condiciones.fecha_ultimo_vencimiento)
        self.assertEqual(condiciones.base_calculo['tasa_comision'], '10')

        condiciones.moneda = 'USD'
        with self.assertRaises(ValidationError):
            condiciones.save()
