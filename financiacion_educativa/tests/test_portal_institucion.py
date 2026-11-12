from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from financiacion_educativa.choices import EstadoSolicitudFinanciacion as Estado
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services import recaudos
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO, DatosRecaudosMixin
from instituciones.models import MembresiaInstitucion
from instituciones.services.membresias import crear_membresia, desactivar_membresia


class DatosPortalInstitucion(DatosRecaudosMixin):
    def preparar_portal(self):
        self.preparar_recaudos()
        self.autorizada = self.solicitud
        self.sin_autorizacion = crear_solicitud(institucion=self.institucion,
            referencia='SYNTHETIC-APPROVED-PENDING', correo='sin-autorizacion@example.test')
        self.sin_autorizacion.estado = Estado.APPROVED
        self.sin_autorizacion.save(update_fields=['estado'])
        self.pendiente = crear_solicitud(institucion=self.institucion,
            referencia='SYNTHETIC-EN-TRAMITE', correo='tramite@example.test')
        self.ajena, _, _, _ = self.contrato('SYNTHETIC-OTHER-INSTITUTION', institucion=self.otra)
        self.usuario = get_user_model().objects.create_user('institucion-sintetica', 'consulta@example.test')
        self.usuario_ajeno = get_user_model().objects.create_user('otra-institucion', 'otra@example.test')
        self.membresia = crear_membresia(usuario=self.usuario, institucion=self.institucion,
            rol=MembresiaInstitucion.Rol.INSTITUTION_READ_ONLY, actor=self.actor)
        crear_membresia(usuario=self.usuario_ajeno, institucion=self.otra,
            rol=MembresiaInstitucion.Rol.INSTITUTION_READ_ONLY, actor=self.actor)

    def ruta(self, nombre, *args):
        return reverse('financiacion_educativa_web:institucion:' + nombre, args=args)


@override_settings(**AISLAMIENTO)
class PortalInstitucionTests(DatosPortalInstitucion, TestCase):
    def setUp(self):
        self.preparar_portal()
        self.client.force_login(self.usuario)

    def test_aprobacion_no_equivale_a_autorizacion_en_bandeja_y_detalle(self):
        for ruta in ('seguimiento', 'solicitudes'):
            respuesta = self.client.get(self.ruta(ruta))
            filas = {item['id']: item for item in respuesta.context['pagina']}
            aprobada = filas[self.sin_autorizacion.pk]
            self.assertTrue(aprobada['solicitud_aprobada'])
            self.assertFalse(aprobada['curso_autorizado'])
            self.assertIsNone(aprobada['autorizacion_efectiva_en'])
            self.assertEqual(aprobada['estado']['etiqueta'], 'Aprobada')
            self.assertNotContains(respuesta, 'Aprobada; curso autorizado')
            self.assertContains(respuesta, 'Solicitud aprobada')
            self.assertContains(respuesta, 'Curso autorizado')
            self.assertContains(respuesta, self.autorizada.referencia_externa)
        detalle = self.client.get(self.ruta('solicitud-detalle', self.sin_autorizacion.pk))
        self.assertTrue(detalle.context['solicitud']['solicitud_aprobada'])
        self.assertFalse(detalle.context['solicitud']['curso_autorizado'])
        self.assertContains(detalle, 'La solicitud est&aacute; aprobada, pero el curso a&uacute;n no est&aacute; autorizado')
        self.assertEqual(detalle.context['solicitud']['seguimiento'], 'Autorizacion del curso pendiente')
        inicio = self.client.get(self.ruta('inicio'))
        self.assertNotContains(inicio, '<small>Curso autorizado</small>', html=True)

    def test_autorizacion_efectiva_reutiliza_firma_contractual_coherente(self):
        firma = self.autorizada.procesos_firma.get()
        detalle = self.client.get(self.ruta('solicitud-detalle', self.autorizada.pk))
        resultado = detalle.context['solicitud']
        self.assertTrue(resultado['solicitud_aprobada'])
        self.assertTrue(resultado['curso_autorizado'])
        self.assertEqual(resultado['autorizacion_efectiva_en'], firma.firmado_en)
        self.assertContains(detalle, 'Autorizaci&oacute;n efectiva desde')
        filas = {item['id']: item for item in self.client.get(self.ruta('seguimiento')).context['pagina']}
        self.assertTrue(filas[self.autorizada.pk]['curso_autorizado'])
        self.assertEqual(filas[self.autorizada.pk]['autorizacion_efectiva_en'], firma.firmado_en)

    def test_firma_no_vigente_no_autoriza_aunque_solicitud_este_aprobada(self):
        artefacto = self.autorizada.artefactos_contractuales.get()
        artefacto.vigente = False
        artefacto.save(update_fields=['vigente'])
        for ruta in ('seguimiento', 'solicitudes'):
            filas = {item['id']: item for item in self.client.get(self.ruta(ruta)).context['pagina']}
            self.assertFalse(filas[self.autorizada.pk]['curso_autorizado'])
        detalle = self.client.get(self.ruta('solicitud-detalle', self.autorizada.pk))
        self.assertTrue(detalle.context['solicitud']['solicitud_aprobada'])
        self.assertFalse(detalle.context['solicitud']['curso_autorizado'])

    def test_simulacion_no_sustituye_autorizacion_contractual_efectiva(self):
        simular_resultado_sandbox(application_id=self.sin_autorizacion.pk, status='APPROVED', actor=self.actor)
        detalle = self.client.get(self.ruta('solicitud-detalle', self.sin_autorizacion.pk))
        self.assertFalse(detalle.context['solicitud']['curso_autorizado'])
        self.assertIsNone(detalle.context['solicitud']['autorizacion_efectiva_en'])

    def test_aislamiento_y_url_directa_no_permiten_consultar_otra_institucion(self):
        for usuario, visible, oculta in ((self.usuario, self.autorizada, self.ajena),
                                         (self.usuario_ajeno, self.ajena, self.autorizada)):
            self.client.force_login(usuario)
            for ruta in ('solicitudes', 'seguimiento'):
                respuesta = self.client.get(self.ruta(ruta), {'institucion_id': oculta.institucion_id})
                self.assertContains(respuesta, visible.referencia_externa)
                self.assertNotContains(respuesta, oculta.referencia_externa)
                self.assertNotIn(oculta.pk, [item['id'] for item in respuesta.context['pagina']])
            for identificador in (oculta.pk, uuid4()):
                respuesta = self.client.get(self.ruta('solicitud-detalle', identificador))
                self.assertEqual(respuesta.status_code, 404)
                self.assertNotContains(respuesta, oculta.referencia_externa, status_code=404)

    def test_membresia_revocada_y_usuario_sin_membresia_no_acceden(self):
        desactivar_membresia(membresia=self.membresia, actor=self.actor)
        for usuario in (self.usuario, self.pagador, self.actor):
            self.client.force_login(usuario)
            for ruta in (self.ruta('seguimiento'), self.ruta('solicitud-detalle', self.autorizada.pk)):
                self.assertEqual(self.client.get(ruta).status_code, 403)

    def test_no_carga_ni_expone_evidencia_bancaria_o_datos_operativos(self):
        banco = self.evidencia()
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        ingreso, _ = recaudos.confirmar_recaudo(**self.datos_confirmacion(reporte, banco,
            referencia='BANK-PRIVATE-REFERENCE', observacion='OPERATIONS-PRIVATE-OBSERVATION'))
        with CaptureQueriesContext(connection) as consultas:
            detalle = self.client.get(self.ruta('solicitud-detalle', self.autorizada.pk))
        for url in (self.ruta('seguimiento'), self.ruta('solicitudes'), self.ruta('solicitud-detalle', self.autorizada.pk)):
            respuesta = self.client.get(url)
            for privado in (str(banco.pk), banco.archivo.name, banco.sha256, str(ingreso.pk),
                ingreso.referencia_verificada, ingreso.observacion, self.actor.username,
                'operaciones/', 'firmas.zapsign', 'TOKEN-DOCUMENTO'):
                self.assertNotContains(respuesta, privado)
        self.assertFalse(any('recaudoeducativo' in q['sql'].lower() for q in consultas))
        soporte = reverse('financiacion_educativa_web:operaciones:recaudo-soporte', args=[reporte.pk])
        propia = reverse('financiacion_educativa_web:operaciones:evidencia-propia', args=[banco.pk])
        self.assertEqual(self.client.get(soporte + '?banco=1').status_code, 403)
        self.assertEqual(self.client.get(propia).status_code, 403)

    def test_filtro_aprobadas_conserva_ambos_resultados_y_excluye_ajenas(self):
        respuesta = self.client.get(self.ruta('seguimiento'), {'estado': 'APPROVED'})
        self.assertEqual({item['id'] for item in respuesta.context['pagina']},
            {self.autorizada.pk, self.sin_autorizacion.pk})
        self.assertNotContains(respuesta, self.ajena.referencia_externa)
        self.assertNotContains(respuesta, self.pendiente.referencia_externa)

    def test_get_no_muta_estado_firma_contrato_o_calendario_y_post_no_decide(self):
        anterior = SolicitudFinanciacionEducativa.objects.values().get(pk=self.autorizada.pk)
        cuotas = list(self.fotografia.cuotas.values())
        artefacto = self.autorizada.artefactos_contractuales.values().get()
        for ruta in (self.ruta('seguimiento'), self.ruta('solicitud-detalle', self.autorizada.pk)):
            self.assertIn('no-store', self.client.get(ruta)['Cache-Control'])
            self.assertEqual(self.client.post(ruta).status_code, 405)
        self.assertEqual(SolicitudFinanciacionEducativa.objects.values().get(pk=self.autorizada.pk), anterior)
        self.assertEqual(list(self.fotografia.cuotas.values()), cuotas)
        self.assertEqual(self.autorizada.artefactos_contractuales.values().get(), artefacto)

    def test_autorizacion_sin_n_mas_uno_ni_llamadas_al_proveedor(self):
        for indice in range(4):
            self.contrato(f'SYNTHETIC-EXTRA-{indice}')
        with CaptureQueriesContext(connection) as consultas, patch(
            'financiacion_educativa.services.firma_zapsign._backend', side_effect=AssertionError('No providers'),
        ):
            respuesta = self.client.get(self.ruta('seguimiento'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertLessEqual(len(consultas), 12)
        self.assertEqual(sum(item['curso_autorizado'] for item in respuesta.context['pagina']), 5)
