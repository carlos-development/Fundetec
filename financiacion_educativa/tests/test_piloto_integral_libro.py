from datetime import date
from decimal import Decimal
from unittest.mock import patch
from io import BytesIO
from django.core.files.base import ContentFile
from pypdf import PdfReader

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models import ArtefactoContractualEducativo, OutboxCorreoEducativo
from financiacion_educativa.models_libro import MovimientoLibroEducativo
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa, ConciliacionRecaudoEducativo
from financiacion_educativa.services import libro_pagos as libro
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.plan_contractual_pdf import datos_plan_contractual, generar_plan_contractual_pdf
from financiacion_educativa.tests.test_gobernanza_libro import DatosGobierno
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True,
                   FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False)
class PilotoIntegralLibroTests(DatosGobierno, TestCase):
    def identidad_contractual(self):
        return (list(self.fotografia.cuotas.values()),
            type(self.fotografia).objects.values().get(pk=self.fotografia.pk),
            list(ArtefactoContractualEducativo.objects.filter(solicitud=self.solicitud).values()),
            obtener_resultado_publico(self.solicitud))

    def comprobar_superficies(self):
        ingresos = ConciliacionRecaudoEducativo.objects.filter(reporte__fotografia=self.fotografia)
        total = libro.resumen_ingresos(ingresos)
        self.assertEqual(total['recibido'], total['capital'] + total['intereses'] + total['sin_destino'])
        prefijo = 'financiacion_educativa_web:operaciones:'
        self.client.force_login(self.actor)
        for ingreso in libro.anotar_ingresos(ingresos):
            self.assertEqual(ingreso.monto_recibido, ingreso.capital_neto + ingreso.interes_neto + ingreso.disponible_libro)
            pagina = self.client.get(reverse(prefijo + 'libro-ingreso', args=[ingreso.pk]))
            self.assertEqual(pagina.status_code, 200)
            self.assertEqual(pagina.context['ingreso'].capital_neto, ingreso.capital_neto)
        tablero = self.client.get(reverse(prefijo + 'tablero-recaudos'), {
            'desde': '2026-07-01', 'hasta': timezone.localdate().isoformat(),
            'institucion': str(self.institucion.pk), 'estado': 'CONFIRMED'})
        self.assertEqual(tablero.context['totales']['confirmados'], total['recibido'])
        self.assertEqual(tablero.context['totales']['capital'], total['capital'])
        self.assertEqual(tablero.context['totales']['intereses'], total['intereses'])
        self.assertEqual(tablero.context['totales']['pendientes'], total['sin_destino'])
        seguimiento = self.client.get(reverse(prefijo + 'seguimiento-detalle', args=[self.solicitud.pk]))
        self.assertEqual(seguimiento.context['resumen_libro'], total)
        self.client.force_login(self.pagador)
        alumno = self.client.get(reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]))
        self.assertEqual(alumno.status_code, 200)
        for ingreso in ingresos:
            self.assertNotContains(alumno, ingreso.referencia_verificada)
            self.assertNotContains(alumno, ingreso.evidencia_movimiento.archivo.name)
            self.assertEqual(self.client.get(reverse(prefijo + 'recaudo-soporte', args=[ingreso.reporte_id]) + '?banco=1').status_code, 403)
        self.client.force_login(self.actor)
        return total

    def ensayar(self, monto, modo, capital, intereses, sin_destino='0', fecha=None):
        identidad = self.identidad_contractual()
        datos_pdf = datos_plan_contractual(self.fotografia)
        ingreso = self.nuevo_ingreso(monto, fecha=fecha)
        antes = OutboxCorreoEducativo.objects.count()
        previo, token = self.preview(ingreso, modo)
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        self.assertEqual(Decimal(previo['resultado']['capital']), Decimal(capital))
        self.assertEqual(Decimal(previo['resultado']['intereses']), Decimal(intereses))
        self.assertEqual(Decimal(previo['resultado']['sin_destino']), Decimal(sin_destino))
        with patch('requests.sessions.Session.request') as proveedor:
            movimiento = self.post(ingreso, token)
        proveedor.assert_not_called()
        total = self.comprobar_superficies()
        self.assertEqual(total['sin_destino'], Decimal(sin_destino))
        self.assertEqual(self.identidad_contractual(), identidad)
        self.assertEqual(datos_plan_contractual(self.fotografia), datos_pdf)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), antes)
        compensacion = libro.compensar(**self.datos_compensacion(movimiento))
        self.assertEqual(compensacion.compensa_id, movimiento.pk)
        total = self.comprobar_superficies()
        self.assertEqual(total['capital'], 0)
        self.assertEqual(total['intereses'], 0)
        self.assertEqual(total['sin_destino'], Decimal(monto))
        self.assertEqual(self.identidad_contractual(), identidad)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 2)

    def test_cuota_completa_conciliar_contabilizar_consultar_compensar_cuadrar(self):
        self.assertEqual(self.fotografia.capital_financiado, Decimal('1142711'))
        self.ensayar('101528', 'FULL', '90101', '11427')

    def test_parcial_con_varias_vencidas_y_comparacion_capital_primero(self):
        estado = libro.estado_libro(self.fotografia)
        alternativo = libro.calcular_distribucion(foto=self.fotografia, estado=estado,
            recibido='100000', fecha=date(2026, 10, 5), modo='PARTIAL',
            reglas={**libro.REGLAS_SINTETICAS, 'imputacion': 'CAPITAL_FIRST'})
        self.assertEqual(Decimal(alternativo['capital']), 90101)
        self.assertEqual(Decimal(alternativo['intereses']), 9899)
        self.ensayar('100000', 'PARTIAL', '88573', '11427', fecha=date(2026, 10, 5))

    def test_abono_reduce_plazo_y_se_puede_compensar_sin_alterar_plan_firmado(self):
        self.ensayar('400000', 'PRINCIPAL', '394286', '5714')

    def test_total_con_excedente_sin_destino_y_compensacion(self):
        self.ensayar('1153925', 'PAYOFF', '1142711', '5714', sin_destino='5500')

    def test_falta_un_centavo_permanece_confirmado_sin_cierre(self):
        ingreso = self.nuevo_ingreso('1148424.99')
        identidad = self.identidad_contractual()
        with self.assertRaisesMessage(ValidationError, 'centavo'):
            self.preview(ingreso, 'PAYOFF')
        total = self.comprobar_superficies()
        self.assertEqual(total['sin_destino'], Decimal('1148424.99'))
        self.assertFalse(MovimientoLibroEducativo.objects.exists())
        self.assertEqual(self.identidad_contractual(), identidad)

    def test_pago_retroactivo_permanece_confirmado_y_no_reordena(self):
        self.registrar(self.nuevo_ingreso(fecha=date(2026, 7, 20)))
        anterior = list(MovimientoLibroEducativo.objects.values())
        ingreso = self.nuevo_ingreso('50000', fecha=date(2026, 7, 19))
        with self.assertRaisesMessage(ValidationError, 'retroactiva'):
            self.preview(ingreso)
        total = self.comprobar_superficies()
        self.assertEqual(total['sin_destino'], Decimal('50000'))
        self.assertEqual(list(MovimientoLibroEducativo.objects.values()), anterior)

    def test_historico_unico_y_pdf_contractual_sin_cambios(self):
        reporte = self.ingreso()
        original = self.aplicar(reporte)
        self.assertEqual(self.aplicar(reporte).pk, original.pk)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
        datos = datos_plan_contractual(self.fotografia)
        pdf_antes = generar_plan_contractual_pdf(self.fotografia)
        artefacto = ArtefactoContractualEducativo.objects.get(solicitud=self.solicitud, tipo='PROMISSORY_NOTE')
        almacen = artefacto.archivo.storage
        nombre = almacen.save(artefacto.archivo.name, ContentFile(pdf_antes))
        segunda = self.fotografia.cuotas.get(numero=2)
        self.registrar(self.nuevo_ingreso(segunda.valor_cuota, fecha=timezone.localdate()), 'FULL')
        total = self.comprobar_superficies()
        self.assertEqual(total['capital'], original.capital_aplicado + segunda.capital)
        self.assertEqual(total['intereses'], original.interes_aplicado + segunda.interes)
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
        self.assertEqual(datos_plan_contractual(self.fotografia), datos)
        with almacen.open(nombre, 'rb') as original:
            self.assertEqual(original.read(), pdf_antes)
        # Font subset serialization need not be byte-deterministic on a fresh render.
        texto = lambda contenido: [pagina.extract_text() for pagina in PdfReader(BytesIO(contenido)).pages]
        self.assertEqual(texto(generar_plan_contractual_pdf(self.fotografia)), texto(pdf_antes))
