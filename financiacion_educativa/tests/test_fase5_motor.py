from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from financiacion_educativa.services.motor_financiero import (
    calcular_cuota_fija,
    generar_plan_amortizacion,
    sumar_meses,
)


class MotorFinancieroEducativoTests(SimpleTestCase):
    def test_cuota_usa_anualidad_francesa(self):
        cuota = calcular_cuota_fija(
            principal=Decimal('1142711'),
            tasa_mensual_porcentaje=Decimal('1'),
            plazo_meses=3,
        )
        self.assertEqual(cuota, Decimal('388547'))

    def test_interes_sobre_saldo_y_amortizacion_reduce_capital(self):
        plan = generar_plan_amortizacion(
            principal=Decimal('1142711'),
            tasa_mensual_porcentaje=Decimal('1'),
            plazo_meses=3,
            fecha_inicio=date(2026, 7, 31),
        )
        primera, segunda = plan.cuotas[:2]

        self.assertEqual(primera.interes, Decimal('11427'))
        self.assertEqual(primera.capital, Decimal('377120'))
        self.assertLess(segunda.interes, primera.interes)
        self.assertEqual(segunda.saldo_inicial, primera.saldo_final)

    def test_ultima_cuota_absorbe_residuo_y_saldo_final_es_cero(self):
        plan = generar_plan_amortizacion(
            principal=Decimal('1000001'),
            tasa_mensual_porcentaje=Decimal('1.2345'),
            plazo_meses=7,
            fecha_inicio=date(2026, 1, 15),
        )
        self.assertEqual(plan.cuotas[-1].saldo_final, Decimal('0'))
        self.assertEqual(
            sum(cuota.capital for cuota in plan.cuotas),
            Decimal('1000001'),
        )
        self.assertEqual(
            sum(cuota.valor_cuota for cuota in plan.cuotas),
            plan.total_proyectado,
        )

    def test_tasa_cero(self):
        plan = generar_plan_amortizacion(
            principal=Decimal('1000000'),
            tasa_mensual_porcentaje=Decimal('0'),
            plazo_meses=3,
            fecha_inicio=date(2026, 1, 31),
        )
        self.assertEqual(plan.cuota_informativa, Decimal('333333'))
        self.assertEqual(plan.intereses_totales, Decimal('0'))
        self.assertEqual(plan.cuotas[-1].valor_cuota, Decimal('333334'))
        self.assertEqual(plan.cuotas[-1].saldo_final, Decimal('0'))

    def test_rechaza_principal_plazo_y_tasa_invalidos(self):
        with self.assertRaises(ValidationError):
            calcular_cuota_fija(
                principal=Decimal('0'),
                tasa_mensual_porcentaje=Decimal('1'),
                plazo_meses=3,
            )
        with self.assertRaises(ValidationError):
            calcular_cuota_fija(
                principal=Decimal('1000'),
                tasa_mensual_porcentaje=Decimal('1'),
                plazo_meses=0,
            )
        with self.assertRaises(ValidationError):
            calcular_cuota_fija(
                principal=Decimal('1000'),
                tasa_mensual_porcentaje=Decimal('-1'),
                plazo_meses=3,
            )

    def test_fin_de_mes_se_conserva(self):
        self.assertEqual(sumar_meses(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(sumar_meses(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(sumar_meses(date(2026, 2, 28), 1), date(2026, 3, 31))

    def test_dia_se_conserva_cuando_es_posible(self):
        self.assertEqual(sumar_meses(date(2026, 1, 30), 1), date(2026, 2, 28))
        self.assertEqual(sumar_meses(date(2026, 1, 30), 2), date(2026, 3, 30))

    def test_vencimiento_no_se_mueve_si_cae_domingo(self):
        plan = generar_plan_amortizacion(
            principal=Decimal('100000'),
            tasa_mensual_porcentaje=Decimal('1'),
            plazo_meses=1,
            fecha_inicio=date(2026, 12, 31),
        )
        self.assertEqual(plan.cuotas[0].fecha_vencimiento, date(2027, 1, 31))
        self.assertEqual(plan.cuotas[0].fecha_vencimiento.weekday(), 6)
