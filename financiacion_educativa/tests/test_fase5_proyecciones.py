from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from financiacion_educativa.models import ParticipanteFinanciacion
from financiacion_educativa.services.proyecciones_financieras import (
    calcular_saldo_proyectado,
    proyectar_abono_capital,
    proyectar_pago_total,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)


class ProyeccionesFinancierasTests(TestCase):
    def setUp(self):
        crear_configuracion_financiera()
        self.solicitud = crear_solicitud()
        self.solicitud.plazo_meses = 3
        self.solicitud.save(update_fields=['plazo_meses'])
        self.fotografia = crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 7, 31),
            fecha_calculo=timezone.make_aware(datetime(2026, 7, 25, 12, 0)),
        )
        self.participante = ParticipanteFinanciacion.objects.create(
            solicitud=self.solicitud,
            nombres='Pagante',
            apellidos='Declarado',
            tipo_documento='CC',
            numero_documento='10000123',
            fecha_nacimiento=date(1990, 1, 1),
        )

    def test_saldo_se_deriva_del_plan_sin_persistir_pagos(self):
        saldo = calcular_saldo_proyectado(
            fotografia=self.fotografia,
            cuotas_cubiertas=1,
        )
        self.assertEqual(saldo.saldo_capital, Decimal('765591'))
        self.assertEqual(saldo.fecha_ultimo_corte, date(2026, 8, 31))
        self.assertEqual(self.fotografia.cuotas.count(), 3)

    def test_abono_entre_vencimientos_cobra_interes_diario_y_reduce_capital(self):
        resultado = proyectar_abono_capital(
            fotografia=self.fotografia,
            valor_pago=Decimal('500000'),
            fecha_efectiva=date(2026, 8, 15),
            participante_pagante_id=self.participante.pk,
        )

        self.assertEqual(resultado.intereses_causados, Decimal('5714'))
        self.assertEqual(resultado.aplicado_intereses, Decimal('5714'))
        self.assertEqual(resultado.aplicado_capital, Decimal('494286'))
        self.assertEqual(resultado.saldo_posterior, Decimal('648425'))
        self.assertEqual(resultado.cuota_programada, Decimal('388547'))
        self.assertLess(resultado.nueva_cantidad_cuotas, 3)
        self.assertGreater(resultado.intereses_futuros_evitados, 0)

    def test_abono_en_vencimiento_usa_interes_mensual_completo(self):
        resultado = proyectar_abono_capital(
            fotografia=self.fotografia,
            valor_pago=Decimal('388547'),
            fecha_efectiva=date(2026, 8, 31),
        )

        self.assertEqual(resultado.dias_causados, 30)
        self.assertEqual(resultado.intereses_causados, Decimal('11427'))
        self.assertEqual(resultado.aplicado_capital, Decimal('377120'))
        self.assertEqual(resultado.saldo_posterior, Decimal('765591'))

    def test_pago_menor_al_interes_no_reduce_capital(self):
        resultado = proyectar_abono_capital(
            fotografia=self.fotografia,
            valor_pago=Decimal('1000'),
            fecha_efectiva=date(2026, 8, 15),
        )
        self.assertEqual(resultado.aplicado_capital, Decimal('0'))
        self.assertEqual(resultado.saldo_posterior, Decimal('1142711'))
        self.assertEqual(resultado.interes_pendiente, Decimal('4714'))

    def test_pago_superior_genera_excedente_sin_saldo_negativo(self):
        resultado = proyectar_abono_capital(
            fotografia=self.fotografia,
            valor_pago=Decimal('2000000'),
            fecha_efectiva=date(2026, 7, 31),
        )
        self.assertEqual(resultado.saldo_posterior, Decimal('0'))
        self.assertEqual(resultado.aplicado_capital, Decimal('1142711'))
        self.assertEqual(resultado.excedente, Decimal('857289'))
        self.assertEqual(resultado.nuevo_plan, ())

    def test_rechaza_fecha_anterior_al_corte_y_posterior_al_siguiente_vencimiento(self):
        with self.assertRaises(ValidationError):
            proyectar_abono_capital(
                fotografia=self.fotografia,
                valor_pago=Decimal('100000'),
                fecha_efectiva=date(2026, 7, 30),
            )
        with self.assertRaises(ValidationError):
            proyectar_abono_capital(
                fotografia=self.fotografia,
                valor_pago=Decimal('100000'),
                fecha_efectiva=date(2026, 9, 1),
            )
        with self.assertRaises(ValidationError):
            proyectar_pago_total(
                fotografia=self.fotografia,
                cuotas_cubiertas=3,
                fecha_efectiva=date(2026, 9, 1),
            )

    def test_participante_pagante_debe_pertenecer_a_solicitud(self):
        otra = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='OTRA-PROYECCION',
        )
        ajeno = ParticipanteFinanciacion.objects.create(
            solicitud=otra,
            nombres='Ajeno',
            apellidos='Solicitud',
            tipo_documento='CC',
            numero_documento='99999999',
        )
        with self.assertRaises(ValidationError):
            proyectar_pago_total(
                fotografia=self.fotografia,
                fecha_efectiva=date(2026, 8, 15),
                participante_pagante_id=ajeno.pk,
            )

    def test_pago_total_incluye_solo_capital_e_interes_causado(self):
        resultado = proyectar_pago_total(
            fotografia=self.fotografia,
            fecha_efectiva=date(2026, 8, 15),
        )

        self.assertEqual(resultado.saldo_capital, Decimal('1142711'))
        self.assertEqual(resultado.intereses_causados, Decimal('5714'))
        self.assertEqual(resultado.total_liquidacion, Decimal('1148425'))
        self.assertEqual(resultado.saldo_proyectado_posterior, Decimal('0'))
        self.assertGreater(resultado.intereses_futuros_excluidos, 0)
        self.assertIn('sin recaudo real', resultado.advertencia)

    def test_proyecciones_no_modifican_fotografia_ni_crean_registros(self):
        total_fotografias = type(self.fotografia).objects.count()
        total_cuotas = self.fotografia.cuotas.count()
        proyectar_pago_total(
            fotografia=self.fotografia,
            fecha_efectiva=date(2026, 8, 15),
        )
        proyectar_abono_capital(
            fotografia=self.fotografia,
            valor_pago=Decimal('500000'),
            fecha_efectiva=date(2026, 8, 15),
        )
        self.assertEqual(type(self.fotografia).objects.count(), total_fotografias)
        self.assertEqual(self.fotografia.cuotas.count(), total_cuotas)
