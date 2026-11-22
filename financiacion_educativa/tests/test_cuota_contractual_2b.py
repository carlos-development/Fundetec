from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from financiacion_educativa.models_libro import MovimientoLibroEducativo
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services import libro_pagos as libro
from financiacion_educativa.tests.piloto_libro_helpers import politica_sintetica
from financiacion_educativa.tests.test_libro_pagos import DatosLibro
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class CuotaContractual2BTests(DatosLibro, TestCase):
    def politica_contractual(self, maximo=1):
        self.politica = politica_sintetica(
            self.institucion, self.actor, self.aprobador, 'CUOTA_CONTRACTUAL',
            self.politica.vigente_desde, reemplaza_id=self.politica.pk,
            reglas={'modalidad': libro.MODALIDAD_CUOTA_CONTRACTUAL, 'max_cuotas_vencidas': maximo},
        )

    def assert_pendiente(self, ingreso):
        movimientos = MovimientoLibroEducativo.objects.filter(ingreso=ingreso)
        self.assertFalse(movimientos.exists())
        netos = libro.resumen_ingresos(ConciliacionRecaudoEducativo.objects.filter(pk=ingreso.pk))
        self.assertEqual(netos, {'recibido': ingreso.monto_recibido, 'capital': 0,
            'intereses': 0, 'sin_destino': ingreso.monto_recibido})

    def test_cuota_vencida_usa_amortizacion_guardada_y_reintento_conserva_movimiento(self):
        self.politica_contractual()
        cuota = self.cuota
        ingreso = self.nuevo_ingreso(cuota.valor_cuota, fecha=cuota.fecha_vencimiento + timedelta(days=1))
        _, token = self.preview(ingreso, 'FULL')
        datos = dict(ingreso_id=ingreso.pk, actor=self.actor, token=token, clave=uuid4(),
            motivo='Caso de cuota contractual de QA', confirmar=True)
        movimiento = libro.contabilizar(**datos)
        self.assertEqual((movimiento.capital, movimiento.intereses), (cuota.capital, cuota.interes))
        self.assertEqual(set(movimiento.lineas.values_list('cuota_id', flat=True)), {cuota.pk})
        self.assertEqual(libro.contabilizar(**datos).pk, movimiento.pk)
        self.assertEqual(MovimientoLibroEducativo.objects.filter(ingreso=ingreso).count(), 1)

    def test_cuota_anticipada_no_recalcula_interes(self):
        self.politica_contractual(maximo=0)
        ingreso = self.nuevo_ingreso(self.cuota.valor_cuota, fecha=self.fecha)
        movimiento = self.registrar(ingreso, 'FULL')
        self.assertEqual(movimiento.intereses, self.cuota.interes)

    def test_otro_modo_se_rechaza_en_preview_y_en_confirmacion(self):
        self.politica_contractual()
        ingreso = self.nuevo_ingreso(self.cuota.valor_cuota)
        for modo in ('PARTIAL', 'PRINCIPAL', 'PAYOFF'):
            with self.subTest(modo=modo):
                with self.assertRaises(ValidationError) as error:
                    self.preview(ingreso, modo)
                self.assertEqual(error.exception.code, 'CONTRACTUAL_INSTALLMENT_MODE_REQUIRED')
        snapshot, _ = self.preview(ingreso, 'FULL')
        token = signing.dumps({**snapshot, 'modo': 'PARTIAL'}, salt=libro.SALT, compress=True)
        with self.assertRaises(ValidationError) as error:
            libro.contabilizar(ingreso_id=ingreso.pk, actor=self.actor, token=token,
                clave=uuid4(), motivo='Modo incompatible en la vista previa', confirmar=True)
        self.assertEqual(error.exception.code, 'CONTRACTUAL_INSTALLMENT_MODE_REQUIRED')
        self.assert_pendiente(ingreso)

    def test_diferencias_de_un_centavo_permanecen_conciliadas(self):
        self.politica_contractual()
        for delta in (Decimal('-0.01'), Decimal('0.01')):
            with self.subTest(delta=delta):
                ingreso = self.nuevo_ingreso(self.cuota.valor_cuota + delta)
                with self.assertRaises(ValidationError) as error:
                    self.preview(ingreso, 'FULL')
                self.assertEqual(error.exception.code, 'INSTALLMENT_AMOUNT_MISMATCH')
                self.assert_pendiente(ingreso)

    def assert_limite_vencidas(self, maximo):
        self.politica_contractual(maximo)
        cuota = self.fotografia.cuotas.get(numero=maximo + 1)
        ingreso = self.nuevo_ingreso(self.cuota.valor_cuota, fecha=cuota.fecha_vencimiento + timedelta(days=1))
        with self.assertRaises(ValidationError) as error:
            self.preview(ingreso, 'FULL')
        self.assertEqual(error.exception.code, 'OVERDUE_INSTALLMENT_LIMIT')
        self.assert_pendiente(ingreso)

    def test_maximo_uno_rechaza_dos_vencidas(self):
        self.assert_limite_vencidas(1)

    def test_maximo_cero_rechaza_una_vencida(self):
        self.assert_limite_vencidas(0)

    def test_formulario_crea_politica_contractual_sin_orden_de_imputacion(self):
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:operaciones:politicas-libro')
        respuesta = self.client.post(url, {
            'institucion': self.institucion.pk, 'version': 'CONTRACTUAL_FORM',
            'vigente_desde': self.fecha.isoformat(), 'es_sintetica': 'on',
            'respaldo': 'Ensayo sintético', 'modalidad': libro.MODALIDAD_CUOTA_CONTRACTUAL,
            'max_cuotas_vencidas': '1',
        }, follow=True)
        self.assertContains(respuesta, 'Cuota contractual completa')
        self.assertNotContains(respuesta, 'Orden de imputacion')

    def test_politica_general_conserva_la_aplicacion_parcial(self):
        ingreso = self.nuevo_ingreso('100000.01')
        movimiento = self.registrar(ingreso, 'PARTIAL')
        self.assertEqual(movimiento.capital + movimiento.intereses, Decimal('100000.01'))
