from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from financiacion_educativa.models import (
    ArtefactoContractualEducativo, CondicionesFinancieras, CuotaAmortizacionEducativa,
    OutboxCorreoEducativo, ProcesoFirmaEducativa,
)
from financiacion_educativa.services import aplicacion_cuotas
from financiacion_educativa.services.plan_contractual_pdf import datos_plan_contractual
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.templatetags.recaudo_format import cop_recaudo
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


AISLAR_PLAN = {**AISLAMIENTO, 'FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED': False}


class DatosPlanContractual(DatosIngresoOperaciones):
    def ruta_plan(self, solicitud=None, fotografia=None):
        return reverse('financiacion_educativa_web:plan-contractual-pdf',
            args=[(solicitud or self.solicitud).pk, (fotografia or self.fotografia).pk])

    def aplicar_primera(self):
        ingreso, _ = self.registrar()
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            actor=self.actor, vista_previa=token, confirmar_cuota=True)

    def texto_pdf(self, respuesta):
        self.assertEqual(respuesta.status_code, 200)
        return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(respuesta.content)).pages)


@override_settings(**AISLAR_PLAN)
class PlanContractualPdfTests(DatosPlanContractual, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.pagador)

    def test_titular_descarga_pdf_real_completo_y_headers_privados(self):
        respuesta = self.client.get(self.ruta_plan())
        texto = self.texto_pdf(respuesta)
        self.assertTrue(respuesta.content.startswith(b'%PDF-'))
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertEqual(respuesta['Content-Disposition'], 'attachment; filename="plan-contractual-v1.pdf"')
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(respuesta['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(respuesta['Referrer-Policy'], 'no-referrer')
        self.assertIn('Plan contractual de pagos', texto)
        self.assertIn('No es saldo vigente ni liquidaci', texto)
        self.assertIn('REC-SINTETICO', texto)
        self.assertIn('Versi', texto)
        self.assertIn(self.fotografia.version_regla, texto)
        self.assertNotIn(self.pagador.email, texto)
        self.assertNotIn(self.actor.username, texto)

    def test_importes_y_todas_las_fechas_son_los_guardados(self):
        texto = self.texto_pdf(self.client.get(self.ruta_plan()))
        fotografia = self.fotografia
        for campo in ('valor_financiado', 'valor_comision', 'valor_iva_comision', 'valor_fondo_garantias',
                      'valor_seguro_vida', 'capital_financiado', 'interes_total_estimado',
                      'total_estimado', 'valor_cuota_estimada'):
            self.assertIn(cop_recaudo(getattr(fotografia, campo)), texto)
        for cuota in fotografia.cuotas.order_by('numero'):
            self.assertIn(cuota.fecha_vencimiento.strftime('%d/%m/%Y'), texto)
            for campo in ('capital', 'interes', 'valor_cuota'):
                self.assertIn(cop_recaudo(getattr(cuota, campo)), texto)
        self.assertIn(fotografia.fecha_inicio_plan.strftime('%d/%m/%Y'), texto)

    def test_datos_y_totales_no_redondean_ni_inventan_valores(self):
        datos = datos_plan_contractual(self.fotografia)
        cuotas = list(self.fotografia.cuotas.order_by('numero').values(
            'numero', 'fecha_vencimiento', 'capital', 'interes', 'valor_cuota'))
        self.assertEqual(datos['cuotas'], cuotas)
        self.assertEqual(datos['totales']['capital'], self.fotografia.capital_financiado)
        self.assertEqual(datos['totales']['interes'], self.fotografia.interes_total_estimado)
        self.assertEqual(datos['totales']['valor_cuota'], self.fotografia.total_estimado)
        self.assertIsInstance(datos['totales']['capital'], Decimal)

    def test_no_consulta_configuracion_actual_ni_simula_y_no_usa_datos_nuevos_de_api(self):
        self.solicitud.valor_plan = Decimal('876543.21')
        self.solicitud.plazo_meses = 1
        self.solicitud.save(update_fields=['valor_plan', 'plazo_meses'])
        with patch('financiacion_educativa.services.configuracion_financiera.seleccionar_configuracion_vigente', side_effect=AssertionError), \
             patch('financiacion_educativa.services.reglas_financieras.calcular_condiciones_financieras', side_effect=AssertionError):
            texto = self.texto_pdf(self.client.get(self.ruta_plan()))
        self.assertIn(cop_recaudo(self.fotografia.valor_financiado), texto)
        self.assertNotIn(cop_recaudo(self.solicitud.valor_plan), texto)

    def test_otra_cuenta_incluso_superusuario_no_descarga(self):
        self.client.force_login(self.actor)
        with patch('financiacion_educativa.web.plan_contractual.generar_plan_contractual_pdf') as generar:
            self.assertEqual(self.client.get(self.ruta_plan()).status_code, 404)
            generar.assert_not_called()

    def test_no_amplia_titularidad_por_coincidencia_de_correo(self):
        self.actor.email = self.pagador.email
        self.actor.save(update_fields=['email'])
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(self.ruta_plan()).status_code, 404)

    def test_sin_sesion_redirige_al_acceso_y_no_renderiza(self):
        self.client.logout()
        with patch('financiacion_educativa.web.plan_contractual.generar_plan_contractual_pdf') as generar:
            respuesta = self.client.get(self.ruta_plan())
            self.assertEqual(respuesta.status_code, 302)
            self.assertIn('/financiacion-educativa/acceso/', respuesta.url)
            generar.assert_not_called()

    def test_fotografia_de_otro_expediente_o_uuid_invalido_no_se_sustituye(self):
        _, otra_foto, _, _ = self.contrato('OTRO-PLAN-SINTETICO', self.otra)
        for foto_id in (otra_foto.pk, uuid4()):
            ruta = reverse('financiacion_educativa_web:plan-contractual-pdf', args=[self.solicitud.pk, foto_id])
            self.assertEqual(self.client.get(ruta).status_code, 404)

    def test_approved_sin_firma_coherente_y_simulacion_no_autorizan(self):
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=self.solicitud)
        ProcesoFirmaEducativa.objects.filter(pk=proceso.pk).update(estado='SENT')
        self.assertEqual(self.client.get(self.ruta_plan()).status_code, 404)
        pagina = self.client.get(reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]))
        self.assertNotContains(pagina, self.ruta_plan())
        simular_resultado_sandbox(application_id=self.solicitud.pk, status='APPROVED', actor=self.actor)
        self.assertEqual(self.client.get(self.ruta_plan()).status_code, 404)

    def test_foto_no_vigente_no_cambia_silenciosamente_de_version(self):
        CondicionesFinancieras.objects.filter(pk=self.fotografia.pk).update(activa=False)
        self.assertEqual(self.client.get(self.ruta_plan()).status_code, 404)

    def test_calendario_incompleto_responde_409_sin_recalcular(self):
        CondicionesFinancieras.objects.filter(pk=self.fotografia.pk).update(plazo_meses=99)
        respuesta = self.client.get(self.ruta_plan())
        self.assertContains(respuesta, 'Plan contractual no disponible', status_code=409)
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(self.fotografia.cuotas.count(), 12)

    def test_importes_o_fechas_incoherentes_fallan_cerrados(self):
        original = self.cuota.fecha_vencimiento
        for cambio in ({'valor_cuota': self.cuota.valor_cuota + 1},
                       {'fecha_vencimiento': original + timedelta(days=1)}):
            with self.subTest(cambio=cambio):
                CuotaAmortizacionEducativa.objects.filter(pk=self.cuota.pk).update(**cambio)
                self.assertEqual(self.client.get(self.ruta_plan()).status_code, 409)
                CuotaAmortizacionEducativa.objects.filter(pk=self.cuota.pk).update(
                    valor_cuota=self.cuota.valor_cuota, fecha_vencimiento=original)

    def test_pago_aplicado_no_altera_pdf_ni_excluye_cuotas_y_no_envia_correo(self):
        antes = self.texto_pdf(self.client.get(self.ruta_plan()))
        self.aplicar_primera()
        estado = list(OutboxCorreoEducativo.objects.values())
        despues = self.texto_pdf(self.client.get(self.ruta_plan()))
        self.assertEqual(antes, despues)
        self.assertEqual(list(OutboxCorreoEducativo.objects.values()), estado)
        self.assertIn(self.cuota.fecha_vencimiento.strftime('%d/%m/%Y'), despues)

    def test_descarga_no_muta_contrato_calendario_o_outbox(self):
        modelos = (ArtefactoContractualEducativo, CondicionesFinancieras, CuotaAmortizacionEducativa, OutboxCorreoEducativo)
        antes = [list(m.objects.order_by('pk').values()) for m in modelos]
        self.assertEqual(self.client.get(self.ruta_plan()).status_code, 200)
        self.assertEqual(antes, [list(m.objects.order_by('pk').values()) for m in modelos])

    def test_disponible_con_canal_recaudos_apagado_y_solo_get(self):
        with self.settings(FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=False):
            self.assertEqual(self.client.get(self.ruta_plan()).status_code, 200)
        self.assertEqual(self.client.post(self.ruta_plan()).status_code, 405)

    def test_enlace_accesible_y_versionado_en_portal_sin_javascript(self):
        respuesta = self.client.get(reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]))
        self.assertContains(respuesta, self.ruta_plan())
        self.assertContains(respuesta, 'Descargar plan contractual en PDF')

    def test_muestra_pdf_sintetica_y_render_de_todas_las_paginas(self):
        pdf = self.client.get(self.ruta_plan()).content
        documento = PdfReader(BytesIO(pdf))
        self.assertGreater(len(documento.pages), 0)
        if not os.environ.get('EDU_PLAN_PDF_SAMPLE'):
            return
        import pypdfium2 as pdfium

        destino = Path(os.environ['EDU_PLAN_PDF_SAMPLE'])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(pdf)
        imagenes = Path(os.environ['EDU_PLAN_SCREENSHOTS'])
        imagenes.mkdir(parents=True, exist_ok=True)
        with pdfium.PdfDocument(pdf) as render:
            for numero, pagina in enumerate(render):
                try:
                    bitmap = pagina.render(scale=1.6)
                    try:
                        bitmap.to_pil().save(imagenes / f'plan-pagina-{numero + 1:02}.png')
                    finally:
                        bitmap.close()
                finally:
                    pagina.close()
