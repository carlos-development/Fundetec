from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models import ArtefactoContractualEducativo, ProcesoFirmaEducativa
from financiacion_educativa.models_recaudos import AplicacionCuotaEducativa
from financiacion_educativa.services import aplicacion_cuotas
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.tests import test_recaudos_breb as base
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(**base.AISLAMIENTO)
class PortalEstudianteTests(DatosIngresoOperaciones, TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        with patch('django.utils.timezone.localdate', return_value=hoy - timedelta(days=90)):
            super().setUp()
        self.datos['fecha'] = hoy
        self.client.force_login(self.pagador)
        self.lista = reverse('financiacion_educativa_web:reanudar-solicitudes')
        self.finanzas = reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk])

    def aplicar(self, ingreso):
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            actor=self.actor, vista_previa=token, confirmar_cuota=True)

    def test_tramite_y_financiacion_siempre_accesibles_en_la_misma_cuenta(self):
        pendiente = crear_solicitud(institucion=self.institucion, usuario=self.pagador, referencia='PORTAL-PENDIENTE')
        respuesta = self.client.get(self.lista)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([o['solicitud'].pk for o in respuesta.context['tramites']], [pendiente.pk])
        self.assertEqual([o['solicitud'].pk for o in respuesta.context['financiaciones']], [self.solicitud.pk])
        self.assertContains(respuesta, 'Financiaciones aprobadas')
        aprobadas = self.client.get(self.lista, {'seccion': 'financiaciones'})
        self.assertContains(aprobadas, 'En tr&aacute;mite')
        self.assertContains(aprobadas, self.finanzas)
        self.assertContains(aprobadas, 'Ver financiaci&oacute;n')

    def test_approved_sin_firma_no_desaparece_ni_habilita_portal_o_pagos(self):
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=self.solicitud)
        ProcesoFirmaEducativa.objects.filter(pk=proceso.pk).update(estado='SENT')
        respuesta = self.client.get(self.lista)
        self.assertTrue(respuesta.context['tramites'][0]['pendiente_autorizacion'])
        self.assertNotContains(respuesta, self.finanzas)
        self.assertContains(respuesta, 'no se habilitan pagos')
        directo = self.client.get(self.finanzas)
        self.assertContains(directo, 'Autorizaci&oacute;n contractual pendiente')
        pago = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        self.assertNotContains(directo, pago)
        self.assertEqual(self.client.get(pago).status_code, 404)

    def test_firma_no_vigente_o_incoherente_tampoco_autoriza(self):
        artefacto = ArtefactoContractualEducativo.objects.get(solicitud=self.solicitud)
        for cambio in ({'vigente': False}, {'archivo_firmado': ''}, {'hash_firmado_sha256': ''}, {'firmado_en': None}):
            original = ArtefactoContractualEducativo.objects.values(*cambio).get(pk=artefacto.pk)
            with self.subTest(cambio=cambio):
                ArtefactoContractualEducativo.objects.filter(pk=artefacto.pk).update(**cambio)
                self.assertEqual(self.client.get(self.lista).context['financiaciones'], [])
                self.assertContains(self.client.get(self.finanzas), 'No se habilitan pagos')
            ArtefactoContractualEducativo.objects.filter(pk=artefacto.pk).update(**original)

    def test_otra_cuenta_aun_con_correo_coincidente_no_accede_por_enlace(self):
        ingreso, _ = self.registrar()
        otro = get_user_model().objects.create_user('portal-otro', self.pagador.email, 'test-only')
        self.client.force_login(otro)
        self.assertNotContains(self.client.get(self.lista), self.solicitud.nombre_curso)
        for nombre, args in (
            ('finanzas', [self.solicitud.pk]), ('pagos', [self.solicitud.pk]),
            ('reporte-pago', [self.solicitud.pk, ingreso.reporte_id]),
        ):
            with self.subTest(ruta=nombre):
                self.assertEqual(self.client.get(reverse('financiacion_educativa_web:' + nombre, args=args)).status_code, 404)

    def test_primera_cuota_incluye_vencida_y_excluye_aplicadas(self):
        ingreso, _ = self.registrar()
        self.aplicar(ingreso)
        respuesta = self.client.get(self.finanzas)
        pendientes = list(self.fotografia.cuotas.order_by('numero'))
        self.assertEqual(respuesta.context['primera_cuota_pendiente']['numero'], pendientes[1].numero)
        self.assertLess(respuesta.context['primera_cuota_pendiente']['fecha_vencimiento'], timezone.localdate())
        self.assertTrue(respuesta.context['cuotas_portal'][0]['aplicada'])
        self.assertEqual(respuesta.context['cuotas_aplicadas'], 1)

    def test_transferencia_confirmada_no_marca_cuota_pagada_y_no_filtra_privados(self):
        ingreso, _ = self.registrar()
        respuesta = self.client.get(self.finanzas)
        self.assertEqual(respuesta.context['primera_cuota_pendiente']['numero'], self.cuota.numero)
        self.assertEqual(respuesta.context['cuotas_aplicadas'], 0)
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())
        self.assertTrue(respuesta.context['hay_confirmadas_por_aplicar'])
        self.assertEqual(respuesta.context['historial_portal'][0]['estado'], 'Transferencia confirmada')
        for privado in (ingreso.referencia_verificada, str(self.banco.pk), self.banco.archivo.name, self.actor.username):
            self.assertNotContains(respuesta, privado)
            self.assertNotIn(privado, str(respuesta.context['historial_portal']))
        url = reverse('financiacion_educativa_web:operaciones:recaudo-soporte', args=[ingreso.reporte_id])
        self.assertEqual(self.client.get(url + '?banco=1').status_code, 403)

    def test_aplicada_se_muestra_como_pago_aplicado(self):
        ingreso, _ = self.registrar()
        aplicacion = self.aplicar(ingreso)
        respuesta = self.client.get(self.finanzas)
        self.assertEqual(respuesta.context['historial_portal'][0]['estado'], 'Pago aplicado')
        self.assertEqual(respuesta.context['historial_portal'][0]['cuota'], aplicacion.cuota.numero)
        self.assertFalse(respuesta.context['hay_confirmadas_por_aplicar'])

    def test_formulario_breb_sugiere_la_primera_sin_aplicar_incluso_vencida(self):
        ingreso, _ = self.registrar()
        self.aplicar(ingreso)
        url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        respuesta = self.client.get(url)
        siguiente = self.fotografia.cuotas.order_by('numero')[1]
        self.assertEqual(respuesta.context['form'].initial['monto'], siguiente.valor_cuota)
        self.assertLess(siguiente.fecha_vencimiento, timezone.localdate())

    def test_todas_aplicadas_no_muestra_siguiente_cuota_ni_declara_cierre(self):
        from uuid import uuid4
        for cuota in self.fotografia.cuotas.order_by('numero'):
            ingreso, _ = self.registrar(clave=uuid4(), referencia=uuid4().hex, monto=cuota.valor_cuota)
            self.aplicar(ingreso)
        respuesta = self.client.get(self.finanzas)
        self.assertIsNone(respuesta.context['primera_cuota_pendiente'])
        self.assertContains(respuesta, 'Esto no constituye paz y salvo')
        self.assertNotContains(respuesta, 'Pagar y reportar transferencia')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')

    def test_simulacion_no_habilita_financiacion_real(self):
        simular_resultado_sandbox(application_id=self.solicitud.pk, actor=self.actor, status='APPROVED')
        self.assertEqual(self.client.get(self.lista).context['financiaciones'], [])
        self.assertContains(self.client.get(self.finanzas), 'No se habilitan pagos')

    def test_secciones_sin_solicitudes_y_parametros_ajenos_no_amplian_consulta(self):
        otro = get_user_model().objects.create_user('portal-vacio', 'vacio@example.test', 'test-only')
        self.client.force_login(otro)
        for seccion in ('tramites', 'financiaciones', 'https://example.test'):
            respuesta = self.client.get(self.lista, {'seccion': seccion, 'usuario_id': self.pagador.pk})
            self.assertContains(respuesta, 'No tienes solicitudes asociadas')
            self.assertEqual(respuesta.context['financiaciones'], [])

    @override_settings(FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=False)
    def test_canal_apagado_conserva_historial_sin_ofrecer_acciones_no_disponibles(self):
        # Seed before disabling is covered separately; here keep the gate visible.
        respuesta = self.client.get(self.finanzas)
        self.assertTrue(respuesta.context['portal_financiacion'])
        self.assertNotContains(respuesta, 'Pagar y reportar transferencia')
        self.assertContains(respuesta, 'Todav&iacute;a no hay transferencias')
