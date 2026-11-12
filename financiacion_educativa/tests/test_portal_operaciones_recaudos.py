from unittest.mock import patch, PropertyMock
from uuid import uuid4

from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.models_recaudos import (
    AplicacionCuotaEducativa, ArchivoRecaudoEducativo, ConciliacionRecaudoEducativo,
    ReporteRecaudoEducativo,
)
from financiacion_educativa.services import aplicacion_cuotas, recaudos
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.tests.factories import imagen_jpeg_prueba
from financiacion_educativa.web.recaudos import evidencia_propia_view


@override_settings(**AISLAMIENTO)
class PortalOperacionesRecaudosTests(DatosIngresoOperaciones, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.actor)

    def ruta(self, nombre, *args):
        return reverse('financiacion_educativa_web:operaciones:' + nombre, args=args)

    def aplicar(self, ingreso):
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            vista_previa=token, actor=self.actor, confirmar_cuota=True)

    def test_tres_bandejas_derivadas_sin_confundir_confirmacion_con_aplicacion(self):
        pendiente, _ = self.reportar()
        rechazado, _ = self.reportar()
        recaudos.rechazar_reporte(reporte_id=rechazado.pk, actor=self.actor, motivo='Movimiento no localizado.')
        aplicado, _ = self.registrar()
        self.aplicar(aplicado)
        confirmado, _ = self.registrar(clave=uuid4(), referencia='BANK-POR-APLICAR')
        for estado, esperado in (('PENDING', pendiente.pk), ('TO_APPLY', confirmado.reporte_id),
                                 ('APPLIED', aplicado.reporte_id), ('REJECTED', rechazado.pk)):
            with self.subTest(estado=estado):
                pagina = self.client.get(self.ruta('recaudos'), {'estado': estado})
                self.assertEqual([r.pk for r in pagina.context['pagina']], [esperado])
                self.assertEqual([p['cantidad'] for p in pagina.context['pestanas']], [1, 1, 1])
        inicial = self.client.get(self.ruta('recaudos'))
        self.assertEqual([r.pk for r in inicial.context['pagina']], [pendiente.pk])
        self.assertContains(inicial, 'Por verificar')
        self.assertContains(inicial, 'Por aplicar')
        self.assertContains(inicial, 'Aplicados')
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)

    def test_filtros_invalidos_no_exponen_resultados_ni_contadores(self):
        self.registrar()
        for parametros in ({'institucion': uuid4()}, {'estado': 'UNTRUSTED'}, {'desde': 'invalida'}):
            pagina = self.client.get(self.ruta('recaudos'), parametros)
            self.assertEqual(pagina.context['pagina'].paginator.count, 0)
            self.assertEqual([p['cantidad'] for p in pagina.context['pestanas']], [0, 0, 0])

    def test_filtros_paginacion_y_cambio_bandeja_conservan_contexto(self):
        for _ in range(26):
            self.reportar()
        filtros = {'estado': 'PENDING', 'institucion': str(self.institucion.pk),
            'desde': timezone.localdate().isoformat()}
        pagina = self.client.get(self.ruta('recaudos'), {**filtros, 'page': 2})
        self.assertEqual(len(pagina.context['pagina']), 1)
        for fragmento in ('estado=PENDING', 'institucion=' + str(self.institucion.pk), 'desde='):
            self.assertIn(fragmento, pagina.context['querystring'])
        for pestana in pagina.context['pestanas']:
            self.assertIn('institucion=' + str(self.institucion.pk), pestana['querystring'])
            self.assertNotIn('page=', pestana['querystring'])
        otra = self.client.get(self.ruta('recaudos'), {'institucion': str(self.otra.pk)})
        self.assertEqual(otra.context['pagina'].paginator.count, 0)
        self.assertEqual(sum(p['cantidad'] for p in otra.context['pestanas']), 0)

    def test_carga_y_consulta_preservan_datos_clave_y_no_registran_ingreso(self):
        datos = self.post(referencia='REFERENCIA-CONSERVADA', monto='1234.50')
        for accion in ('evidencia', 'actualizar'):
            respuesta = self.client.post(self.url_registro(), {**datos, 'accion': accion,
                **({'archivo': imagen_jpeg_prueba()} if accion == 'evidencia' else {})})
            self.assertEqual(respuesta.status_code, 200)
            form = respuesta.context['form']
            for campo in ('referencia', 'monto', 'cuenta_id', 'clave', 'evidencia_id'):
                self.assertEqual(str(form[campo].value()), str(datos[campo]))
            self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())
        self.assertEqual(ArchivoRecaudoEducativo.objects.filter(finalidad='BANK_EVIDENCE').count(), 2)

    def test_consulta_de_evidencia_con_diferencia_abre_justificacion_sin_javascript(self):
        reporte, _ = self.reportar()
        respuesta = self.client.post(self.ruta('recaudo-evidencia', reporte.pk), {
            'accion': 'actualizar', 'monto': '99000.00', 'referencia': 'BANK-DIFERENCIA',
        })
        self.assertContains(respuesta, 'data-monto-reportado="100000.00" open')
        self.assertEqual(respuesta.context['confirmar_form']['referencia'].value(), 'BANK-DIFERENCIA')
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_carga_invalida_preserva_datos_y_no_simula_archivo(self):
        respuesta = self.client.post(self.url_registro(), {**self.post(), 'accion': 'evidencia'})
        self.assertIn('archivo', respuesta.context['evidencia_form'].errors)
        self.assertEqual(respuesta.context['form']['referencia'].value(), self.datos['referencia'])
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())

    def test_conciliacion_conserva_diferencia_y_observacion_al_revisar_evidencia(self):
        reporte, _ = self.reportar()
        datos = dict(monto='99000.00', fecha=timezone.localdate().isoformat(), referencia='CONSERVAR-BANCO',
            justificacion_diferencia='Diferencia verificada en banco.', observacion='Nota conservada.',
            evidencia_id=str(self.banco.pk))
        for accion in ('evidencia', 'actualizar'):
            respuesta = self.client.post(self.ruta('recaudo-evidencia', reporte.pk), {**datos, 'accion': accion,
                **({'archivo': imagen_jpeg_prueba()} if accion == 'evidencia' else {})})
            for campo, valor in datos.items():
                self.assertEqual(respuesta.context['confirmar_form'][campo].value(), valor)
            self.assertContains(respuesta, 'name="justificacion_diferencia"')
            self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_evidencia_distingue_pendiente_lista_rechazo_y_error_tecnico(self):
        for estado, texto in (('PENDING', 'Revisi&oacute;n pendiente'), ('SAFE', 'Lista para verificar'),
            ('BLOCKED', 'Rechazada por seguridad'), ('ERROR', 'Error t&eacute;cnico de verificaci&oacute;n')):
            with self.subTest(estado=estado), patch.object(ArchivoRecaudoEducativo, 'estado_escaneo',
                    new_callable=PropertyMock, return_value=estado):
                pagina = self.client.get(self.url_registro())
                self.assertContains(pagina, texto)
                preview = self.ruta('evidencia-propia', self.banco.pk)
                if estado == 'SAFE':
                    self.assertContains(pagina, preview)
                else:
                    self.assertNotContains(pagina, preview)
                    self.assertEqual(self.client.get(preview).status_code, 404)

    def test_preview_privada_conserva_conexion_y_no_imprime_ruta(self):
        request = RequestFactory().get(self.ruta('evidencia-propia', self.banco.pk))
        request.user = self.actor
        principal = connection.connection
        respuesta = evidencia_propia_view(request, self.banco.pk)
        try:
            self.assertTrue(b''.join(respuesta.streaming_content))
            self.assertIn('private', respuesta['Cache-Control'])
            self.assertIn('no-store', respuesta['Cache-Control'])
            self.assertEqual(respuesta['X-Content-Type-Options'], 'nosniff')
            self.assertNotIn(self.banco.archivo.name, str(dict(respuesta.headers)))
        finally:
            respuesta.file_to_stream.close()
        self.assertIs(connection.connection, principal)
        self.assertTrue(connection.is_usable())
        self.assertEqual(ArchivoRecaudoEducativo.objects.filter(pk=self.banco.pk).count(), 1)

    def test_preview_exige_permiso_propiedad_finalidad_e_integridad(self):
        url = self.ruta('evidencia-propia', self.banco.pk)
        self.client.force_login(self.pagador)
        self.assertEqual(self.client.get(url).status_code, 403)
        consulta = self.operador((recaudos.PERMISO_CONSULTAR, aplicacion_cuotas.PERMISO_ACCESO))
        self.client.force_login(consulta)
        self.assertEqual(self.client.get(url).status_code, 403)
        otro = self.operador((recaudos.PERMISO_CONSULTAR, recaudos.PERMISO_REGISTRAR,
            aplicacion_cuotas.PERMISO_ACCESO))
        self.client.force_login(otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(self.ruta('evidencia-propia', self.receptor.qr_id)).status_code, 404)
        with self.banco.archivo.open('wb') as archivo:
            archivo.write(b'altered-test-only')
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_recorrido_no_habilita_reparto_propuesto_ni_altera_contrato(self):
        ingreso, _ = self.registrar()
        anterior = list(self.fotografia.cuotas.values())
        self.aplicar(ingreso)
        pagina = self.client.get(self.ruta('recaudo-detalle', ingreso.reporte_id))
        self.assertContains(pagina, 'pagada')
        self.assertNotContains(pagina, '80 %')
        self.assertNotContains(pagina, '20 %')
        self.assertEqual(list(self.fotografia.cuotas.values()), anterior)
