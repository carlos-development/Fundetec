import threading
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch, PropertyMock
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection, connections, close_old_connections, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfWriter

from financiacion_educativa.models_recaudos import (
    ArchivoRecaudoEducativo, IntentoEscaneoRecaudo, ReceptorBrebEducativo,
    ReporteRecaudoEducativo, ConciliacionRecaudoEducativo, RechazoReporteRecaudoEducativo,
)
from financiacion_educativa.services import recaudos as servicios
from financiacion_educativa.services.escaneo_documentos import ResultadoAntivirus, VeredictoAntivirus, ErrorEscaneoDocumento
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.tests.factories import imagen_jpeg_prueba
from financiacion_educativa.tests.test_dashboard_obligaciones import DatosObligacionesMixin
from financiacion_educativa.web.recaudos import comprobante_view, qr_view


class Limpio:
    def escanear(self, archivo):
        archivo.read()
        return ResultadoAntivirus(VeredictoAntivirus.CLEAN, 'synthetic')


class Infectado:
    def escanear(self, archivo):
        return ResultadoAntivirus(VeredictoAntivirus.INFECTED, 'synthetic')


class Fallo:
    def escanear(self, archivo):
        raise ErrorEscaneoDocumento('SCANNER_TIMEOUT')


def pdf_sintetico():
    buffer = BytesIO()
    pdf = PdfWriter()
    pdf.add_blank_page(width=240, height=240)
    pdf.write(buffer)
    return SimpleUploadedFile('sintetico.pdf', buffer.getvalue(), content_type='application/pdf')


class DatosRecaudosMixin(DatosObligacionesMixin):
    def preparar_recaudos(self):
        self.temporal = TemporaryDirectory(prefix='fundetec-recaudos-test-')
        self.addCleanup(self.temporal.cleanup)
        self.settings_privados = override_settings(FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.temporal.name)
        self.settings_privados.enable()
        self.addCleanup(self.settings_privados.disable)
        self.preparar_datos()
        self.pagador = get_user_model().objects.create_user('pagador', 'pagador@example.test', 'test-only')
        self.solicitud, self.fotografia, _, _ = self.contrato('REC-SINTETICO')
        self.solicitud.usuario = self.pagador
        self.solicitud.correo = self.pagador.email
        self.solicitud.save(update_fields=['usuario', 'correo'])
        self.receptor = servicios.crear_receptor(entidad='BANCO-SINTETICO', identificador_cuenta='CUENTA-SINTETICA',
            titular='RECEPTOR DE PRUEBA', llave='LLAVE-SINTETICA', qr=imagen_jpeg_prueba(), actor=self.actor)
        self.escanear(self.receptor.qr_id)
        servicios.activar_receptor(receptor_id=self.receptor.pk, actor=self.actor, motivo='Configuracion sintetica')

    def escanear(self, archivo_id, backend=None):
        return servicios.escanear_archivo_recaudo(archivo_id=archivo_id, backend=backend or Limpio())

    def reportar(self, **cambios):
        datos = dict(solicitud_id=self.solicitud.pk, actor=self.pagador, receptor_id=self.receptor.pk,
            clave=uuid4(), proposito='INSTALLMENT', monto='100000.00', fecha=timezone.localdate(),
            referencia='declarada-no-verificada', archivo=imagen_jpeg_prueba())
        datos.update(cambios)
        return servicios.reportar_pago(**datos)

    def evidencia(self):
        registro = servicios.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=self.actor)
        self.escanear(registro.pk)
        return registro

    def datos_confirmacion(self, reporte, evidencia=None, **cambios):
        evidencia = evidencia or self.evidencia()
        datos = dict(reporte_id=reporte.pk, actor=self.actor, cuenta_id=self.receptor.cuenta_id,
            referencia='BANK-TX-001', monto=reporte.monto_reportado, fecha=timezone.localdate(),
            evidencia_id=evidencia.pk, observacion='Movimiento verificado en el banco sintetico.')
        datos.update(cambios)
        return datos


AISLAMIENTO = dict(
    DEPLOYMENT_ENVIRONMENT='test', FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=True,
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False, FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_QA_MODE=False, EMAIL_LIVE_DELIVERY_ENABLED=False,
)


@override_settings(**AISLAMIENTO)
class RecaudosBrebTests(DatosRecaudosMixin, TestCase):
    def setUp(self):
        self.preparar_recaudos()

    def test_doble_reporte_reutiliza_y_payload_distinto_falla(self):
        clave = uuid4()
        original, replay = self.reportar(clave=clave)
        repetido, replay2 = self.reportar(clave=clave)
        self.assertFalse(replay)
        self.assertTrue(replay2)
        self.assertEqual(original.pk, repetido.pk)
        with self.assertRaises(ValidationError):
            self.reportar(clave=clave, monto='200000')
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)
        self.assertEqual(ArchivoRecaudoEducativo.objects.count(), 2)

    def test_decimal_rechaza_no_finitos_precision_y_no_positivo(self):
        from financiacion_educativa.templatetags.recaudo_format import cop_recaudo
        self.assertEqual(cop_recaudo(Decimal('0.01')), '$0,01')
        for valor in ['0', '-1', 'NaN', 'Infinity', '0.001', '100000000000000']:
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                self.reportar(monto=valor)
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())

    def test_proposito_fecha_y_version_se_validan_en_servidor(self):
        for cambios in [dict(proposito='PAID'), dict(fecha=timezone.localdate() + timedelta(days=1)), dict(receptor_id=uuid4())]:
            with self.subTest(cambios=cambios), self.assertRaises(ValidationError):
                self.reportar(**cambios)

    def test_registro_no_cambia_estado_contrato_ni_resultado_publico(self):
        anterior = obtener_resultado_publico(self.solicitud)
        foto = self.fotografia.__dict__.copy()
        reporte, _ = self.reportar(proposito='FULL_PAYMENT', monto='15000')
        self.escanear(reporte.comprobante_id)
        servicios.confirmar_recaudo(**self.datos_confirmacion(reporte))
        self.solicitud.refresh_from_db()
        self.fotografia.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.assertEqual(obtener_resultado_publico(self.solicitud), anterior)
        self.assertEqual(self.fotografia.capital_financiado, foto['capital_financiado'])
        self.assertEqual(self.fotografia.total_estimado, foto['total_estimado'])

    def test_comprobante_pendiente_bloquea_lectura_y_confirmacion(self):
        reporte, _ = self.reportar()
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pago-comprobante', args=[self.solicitud.pk, reporte.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**self.datos_confirmacion(reporte))

    def test_malware_no_se_consulta_ni_se_confirma(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id, Infectado())
        self.assertEqual(reporte.comprobante.estado_escaneo, 'BLOCKED')
        self.assertIsNone(self.escanear(reporte.comprobante_id))
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**self.datos_confirmacion(reporte))

    def test_timeout_recuperable_con_historial_y_sin_inventar_safe(self):
        reporte, _ = self.reportar()
        intento = self.escanear(reporte.comprobante_id, Fallo())
        self.assertEqual(intento.estado, 'ERROR')
        self.assertEqual(reporte.comprobante.estado_escaneo, 'ERROR')
        self.assertIsNone(self.escanear(reporte.comprobante_id))
        servicios.escanear_archivo_recaudo(archivo_id=reporte.comprobante_id, actor=self.actor, motivo='Recuperacion autorizada', backend=Limpio())
        self.assertEqual(reporte.comprobante.estado_escaneo, 'SAFE')
        self.assertEqual(reporte.comprobante.escaneos.count(), 2)

    def test_intento_abandonado_se_recupera_y_veredicto_ambiguo_falla_cerrado(self):
        reporte, _ = self.reportar()
        with patch('django.utils.timezone.now', return_value=timezone.now() - timedelta(hours=1)):
            viejo = IntentoEscaneoRecaudo.objects.create(archivo=reporte.comprobante, numero=1, sha256=reporte.comprobante.sha256)
        with patch('financiacion_educativa.services.recaudos.obtener_backend_escaneo') as proveedor:
            proveedor.return_value.escanear.return_value = {'clean': True}
            nuevo = servicios.escanear_archivo_recaudo(archivo_id=reporte.comprobante_id, actor=self.actor, motivo='Intento abandonado')
        viejo.refresh_from_db()
        self.assertEqual(viejo.codigo_error, 'ABANDONED_ATTEMPT')
        self.assertEqual(nuevo.estado, 'ERROR')
        self.assertEqual(reporte.comprobante.estado_escaneo, 'ERROR')

    def test_safe_no_se_fabrica_por_orm(self):
        with self.assertRaises(ValidationError):
            IntentoEscaneoRecaudo.objects.create(archivo=self.receptor.qr, numero=2, sha256=self.receptor.qr.sha256, estado='CLEAN')
        with self.assertRaises(ValidationError):
            self.receptor.qr.escaneos.update(estado='CLEAN')
        with self.assertRaises(ValidationError):
            ArchivoRecaudoEducativo.objects.update(sha256='0' * 64)

    def test_formato_real_jpg_png_pdf_y_archivo_invalido(self):
        self.reportar(archivo=pdf_sintetico())
        with self.assertRaises(ValidationError):
            self.reportar(archivo=SimpleUploadedFile('enganoso.pdf', b'%PDF-invalid %%EOF'))
        with override_settings(FINANCIACION_EDUCATIVA_RECAUDO_MAX_BYTES=20), self.assertRaises(ValidationError):
            self.reportar()

    def test_confirmacion_repetida_no_duplica_y_cambio_falla(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte)
        primero, replay = servicios.confirmar_recaudo(**datos)
        segundo, replay2 = servicios.confirmar_recaudo(**datos)
        self.assertFalse(replay)
        self.assertTrue(replay2)
        self.assertEqual(primero.pk, segundo.pk)
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**{**datos, 'monto': '1', 'justificacion_diferencia': 'Cambio'})
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_diferencia_exige_justificacion_explicita_y_preserva_valores(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte, monto='50000')
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**datos)
        ingreso, _ = servicios.confirmar_recaudo(**datos, justificacion_diferencia='El banco registra un importe menor al declarado.')
        self.assertEqual(ingreso.monto_recibido, Decimal('50000'))
        reporte.refresh_from_db()
        self.assertEqual(reporte.monto_reportado, Decimal('100000'))

    def test_evidencia_bancaria_obligatoria_y_no_puede_ser_el_recibo(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte)
        with self.assertRaises(PermissionDenied):
            servicios.confirmar_recaudo(**{**datos, 'evidencia_id': reporte.comprobante_id})
        pendiente = servicios.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=self.actor)
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**{**datos, 'evidencia_id': pendiente.pk})

    def test_mismo_movimiento_global_no_duplica_entre_instituciones(self):
        primero, _ = self.reportar()
        self.escanear(primero.comprobante_id)
        servicios.confirmar_recaudo(**self.datos_confirmacion(primero))
        solicitud, _, _, _ = self.contrato('OTRA-INSTITUCION', self.otra)
        solicitud.usuario, solicitud.correo = self.pagador, self.pagador.email
        solicitud.save(update_fields=['usuario', 'correo'])
        otro, _ = self.reportar(solicitud_id=solicitud.pk)
        self.escanear(otro.comprobante_id)
        with self.assertRaisesMessage(ValidationError, 'El movimiento bancario ya esta conciliado'):
            servicios.confirmar_recaudo(**self.datos_confirmacion(otro, referencia='bank-tx-001'))
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_rechazo_preserva_y_no_rechaza_financiacion(self):
        reporte, _ = self.reportar()
        rechazo = servicios.rechazar_reporte(reporte_id=reporte.pk, actor=self.actor, motivo='No se encontro el movimiento. Verifica la referencia con tu banco.')
        self.assertEqual(servicios.rechazar_reporte(reporte_id=reporte.pk, actor=self.actor, motivo=rechazo.motivo_publico).pk, rechazo.pk)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        with self.assertRaises(ValidationError):
            servicios.confirmar_recaudo(**self.datos_confirmacion(reporte))

    def test_admin_y_orm_no_editan_ni_borran_evidencia_confirmada(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        ingreso, _ = servicios.confirmar_recaudo(**self.datos_confirmacion(reporte))
        with self.assertRaises(ValidationError):
            ingreso.save()
        with self.assertRaises(ValidationError):
            ingreso.delete()
        with self.assertRaises(ValidationError):
            ConciliacionRecaudoEducativo._base_manager.update(monto_recibido=1)
        request = RequestFactory().get('/')
        request.user = self.actor
        self.assertFalse(admin.site._registry[ConciliacionRecaudoEducativo].has_change_permission(request, ingreso))
        self.assertFalse(admin.site._registry[ConciliacionRecaudoEducativo].has_delete_permission(request, ingreso))

    def test_staff_membresia_o_uuid_no_conceden_acceso(self):
        ajeno = get_user_model().objects.create_user('ajeno', 'ajeno@example.test', 'test-only', is_staff=True)
        self.client.force_login(ajeno)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:recaudos')).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.reportar(actor=ajeno)
        with self.assertRaises(PermissionDenied):
            servicios.crear_receptor(entidad='X', identificador_cuenta='X', titular='X', llave='X', qr=imagen_jpeg_prueba(), actor=ajeno)

    def test_consulta_no_concede_decision_y_post_requiere_csrf(self):
        consulta = get_user_model().objects.create_user('consulta', 'consulta@example.test', 'test-only')
        consulta.user_permissions.add(*Permission.objects.filter(codename__in=['acceder_dashboard_operativo', 'consultar_recaudos_educativos']))
        reporte, _ = self.reportar()
        self.client.force_login(consulta)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[reporte.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('financiacion_educativa_web:operaciones:recaudo-confirmar', args=[reporte.pk]), {}).status_code, 403)
        from django.test import Client
        seguro = Client(enforce_csrf_checks=True)
        seguro.force_login(self.pagador)
        self.assertEqual(seguro.post(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]), {}).status_code, 403)

    def test_sin_configuracion_o_flag_no_habilita_instrucciones(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        servicios.activar_receptor(receptor_id=self.receptor.pk, actor=self.actor, motivo='Retirado', habilitado=False)
        self.assertContains(self.client.get(url), 'Canal de pago no disponible')
        with self.assertRaises(ValidationError):
            self.reportar()
        with override_settings(FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=False):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_version_receptor_no_reinterpreta_reportes_historicos(self):
        reporte, _ = self.reportar()
        nuevo = servicios.crear_receptor(entidad='BANCO-SINTETICO', identificador_cuenta='CUENTA-SINTETICA',
            titular='RECEPTOR ACTUALIZADO', llave='NUEVA-SINTETICA', qr=pdf_sintetico(), actor=self.actor)
        self.escanear(nuevo.qr_id)
        servicios.activar_receptor(receptor_id=nuevo.pk, actor=self.actor, motivo='Rotacion sintetica')
        reporte.refresh_from_db()
        self.assertEqual(reporte.receptor_id, self.receptor.pk)
        self.assertEqual(nuevo.cuenta_id, self.receptor.cuenta_id)
        with self.assertRaises(ValidationError):
            self.reportar()

    def _qr_privado(self, receptor, descargar=False):
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, receptor.pk])
        request = RequestFactory().get(url, {'download': '1'} if descargar else {})
        request.user = self.pagador
        respuesta = qr_view(request, self.solicitud.pk, receptor.pk)
        try:
            contenido = b''.join(respuesta.streaming_content)
        finally:
            respuesta.file_to_stream.close()
        return respuesta, contenido

    def test_qr_visualizacion_y_descarga_vinculan_version_mostrada(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        pagina = self.client.get(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]))
        self.assertContains(pagina, f'src="{url}"')
        self.assertContains(pagina, f'href="{url}?download=1"')
        for descargar in (False, True):
            with self.subTest(descargar=descargar):
                respuesta, contenido = self._qr_privado(self.receptor, descargar)
                with self.receptor.qr.archivo.open('rb') as archivo:
                    self.assertEqual(contenido, archivo.read())
                self.assertIn('no-store', respuesta['Cache-Control'])
                self.assertEqual(respuesta['Content-Type'], 'image/jpeg')
                self.assertEqual(respuesta['Content-Disposition'].startswith('attachment'), descargar)
        self.assertTrue(connection.is_usable())
        self.assertEqual(ReceptorBrebEducativo.objects.count(), 1)

    def test_cambio_receptor_entre_apertura_y_descarga_exige_recargar(self):
        self.client.force_login(self.pagador)
        pagina_url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        url_anterior = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        self.assertContains(self.client.get(pagina_url), url_anterior)
        nuevo = servicios.crear_receptor(entidad='BANCO-SINTETICO', identificador_cuenta='CUENTA-SINTETICA',
            titular='RECEPTOR ACTUALIZADO', llave='LLAVE-DOS', qr=pdf_sintetico(), actor=self.actor)
        self.escanear(nuevo.qr_id)
        servicios.activar_receptor(receptor_id=nuevo.pk, actor=self.actor, motivo='Rotacion sintetica')
        self.assertEqual(nuevo.cuenta_id, self.receptor.cuenta_id)
        for descargar in (False, True):
            with self.subTest(descargar=descargar):
                respuesta = self.client.get(url_anterior, {'download': '1'} if descargar else {})
                self.assertContains(respuesta, 'Recarga la pagina de pagos', status_code=409)
                self.assertIn('no-store', respuesta['Cache-Control'])
                self.assertFalse(respuesta.streaming)
        url_actual = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, nuevo.pk])
        pagina = self.client.get(pagina_url)
        self.assertContains(pagina, f'href="{url_actual}"')
        self.assertContains(pagina, f'href="{url_actual}?download=1"')
        self.assertNotContains(pagina, url_anterior)
        respuesta, contenido = self._qr_privado(nuevo, True)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        with nuevo.qr.archivo.open('rb') as archivo:
            self.assertEqual(contenido, archivo.read())

    def test_cambio_de_cuenta_no_sustituye_qr_de_pagina_abierta(self):
        self.client.force_login(self.pagador)
        pagina_url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        url_anterior = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        self.assertContains(self.client.get(pagina_url), url_anterior)
        nuevo = servicios.crear_receptor(entidad='OTRO-BANCO', identificador_cuenta='OTRA-CUENTA',
            titular='OTRO RECEPTOR', llave='OTRA-LLAVE', qr=pdf_sintetico(), actor=self.actor)
        self.escanear(nuevo.qr_id)
        servicios.activar_receptor(receptor_id=nuevo.pk, actor=self.actor, motivo='Cambio de cuenta sintetico')
        self.assertNotEqual(nuevo.cuenta_id, self.receptor.cuenta_id)
        self.assertContains(self.client.get(url_anterior, {'download': '1'}), 'Recarga la pagina de pagos', status_code=409)
        respuesta, contenido = self._qr_privado(nuevo, True)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        with nuevo.qr.archivo.open('rb') as archivo:
            self.assertEqual(contenido, archivo.read())

    def test_qr_version_invalida_o_sin_version_no_se_resuelve_al_vigente(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, uuid4()])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url.rsplit('/', 2)[0] + '/invalida/').status_code, 404)
        self.assertEqual(self.client.get(url.rsplit('/', 2)[0] + '/').status_code, 404)

    def test_qr_version_desactivada_exige_recargar(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        servicios.activar_receptor(receptor_id=self.receptor.pk, actor=self.actor, motivo='Retiro sintetico', habilitado=False)
        self.assertContains(self.client.get(url), 'Recarga la pagina de pagos', status_code=409)

    def test_qr_version_no_concede_acceso_a_usuario_ajeno(self):
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        for descargar in (False, True):
            with self.subTest(descargar=descargar):
                self.assertEqual(self.client.get(url, {'download': '1'} if descargar else {}).status_code, 404)

    def test_qr_version_exige_safe_incluso_si_se_resuelve_como_vigente(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pago-qr', args=[self.solicitud.pk, self.receptor.pk])
        with patch.object(ArchivoRecaudoEducativo, 'estado_escaneo', new_callable=PropertyMock, return_value='PENDING'), \
                patch('financiacion_educativa.web.recaudos.servicios.receptor_vigente', return_value=self.receptor):
            self.assertEqual(self.client.get(url, {'download': '1'}).status_code, 404)

    def test_lectura_segura_preserva_conexion_principal(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        request = RequestFactory().get('/')
        request.user = self.pagador
        principal = connection.connection
        respuesta = comprobante_view(request, self.solicitud.pk, reporte.pk)
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertNotIn(reporte.comprobante.archivo.name, str(respuesta.headers))
        self.assertTrue(b''.join(respuesta.streaming_content))
        respuesta.file_to_stream.close()
        self.assertIs(connection.connection, principal)
        self.assertTrue(connection.is_usable())
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)

    def test_bandeja_suma_sin_joins_duplicados_y_filtra_fechas_bancarias(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        servicios.confirmar_recaudo(**self.datos_confirmacion(reporte, fecha=timezone.localdate() - timedelta(days=1)))
        self.reportar()
        self.client.force_login(self.actor)
        url = reverse('financiacion_educativa_web:operaciones:recaudos')
        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(url)
        self.assertEqual(respuesta.context['pendiente'], Decimal('100000'))
        self.assertEqual(respuesta.context['confirmado'], Decimal('100000'))
        self.assertLessEqual(len(consultas), 12)
        filtrada = self.client.get(url, {'desde': timezone.localdate().isoformat()})
        self.assertEqual(filtrada.context['confirmado'], 0)
        self.assertEqual(filtrada.context['pendiente'], Decimal('100000'))

    @patch('financiacion_educativa.management.commands.procesar_comprobantes_educativos.close_old_connections')
    def test_comando_limitado_y_no_usa_ia(self, _limpieza_del_worker):
        # El comando real no corre dentro de la transaccion abierta de TestCase.
        reporte, _ = self.reportar()
        otro, _ = self.reportar()
        with patch('financiacion_educativa.services.recaudos.obtener_backend_escaneo', return_value=Limpio()):
            call_command('procesar_comprobantes_educativos', once=True, archivo_id=str(reporte.comprobante_id), stdout=StringIO())
        self.assertEqual(reporte.comprobante.estado_escaneo, 'SAFE')
        self.assertEqual(otro.comprobante.estado_escaneo, 'PENDING')

    def test_tutor_solo_accede_por_asociacion_existente_y_no_parentesco(self):
        from financiacion_educativa.models import ParticipanteFinanciacion, RolParticipanteFinanciacion
        from financiacion_educativa.choices import RolParticipante
        tutor = get_user_model().objects.create_user('tutor', 'tutor@example.test', 'test-only')
        participante = ParticipanteFinanciacion.objects.create(solicitud=self.solicitud, usuario=tutor,
            nombres='TUTOR', apellidos='SINTETICO', tipo_documento='CC', numero_documento='SINTETICO1', relacion_verificada=True)
        RolParticipanteFinanciacion.objects.create(solicitud=self.solicitud, participante=participante, rol=RolParticipante.GUARDIAN)
        with self.assertRaises(PermissionDenied):
            self.reportar(actor=tutor)
        self.solicitud.usuario, self.solicitud.correo = tutor, tutor.email
        self.solicitud.save(update_fields=['usuario', 'correo'])
        reporte, _ = self.reportar(actor=tutor)
        self.assertEqual(reporte.reportado_por_id, tutor.pk)

    def test_web_doble_post_payload_y_aislamiento(self):
        self.client.force_login(self.pagador)
        url = reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk])
        self.assertContains(self.client.get(url), f'value="{timezone.localdate().isoformat()}"')
        clave = uuid4()
        datos = dict(clave=str(clave), receptor_id=str(self.receptor.pk), proposito='INSTALLMENT',
            monto='100000.00', fecha=timezone.localdate().isoformat(), referencia='DECLARADA')
        primera = self.client.post(url, {**datos, 'archivo': imagen_jpeg_prueba()})
        segunda = self.client.post(url, {**datos, 'archivo': imagen_jpeg_prueba()})
        self.assertEqual(primera.status_code, 302)
        self.assertEqual(primera['Location'], segunda['Location'])
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)
        tercera = self.client.post(url, {**datos, 'monto': '99999', 'archivo': imagen_jpeg_prueba()})
        self.assertContains(tercera, 'datos diferentes')
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(primera['Location']).status_code, 404)

    def test_formulario_acepta_minimo_decimal_sin_redondearlo_a_cero(self):
        self.client.force_login(self.pagador)
        respuesta = self.client.post(reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]), {
            'clave': str(uuid4()), 'receptor_id': str(self.receptor.pk), 'proposito': 'INSTALLMENT',
            'monto': '0.01', 'fecha': timezone.localdate().isoformat(), 'referencia': '',
            'archivo': imagen_jpeg_prueba(),
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(ReporteRecaudoEducativo.objects.get().monto_reportado, Decimal('0.01'))
        self.assertContains(self.client.get(respuesta['Location']), '$0,01')

    def test_qr_pdf_privado_soportes_png_evidencia_y_estado_seguro(self):
        from PIL import Image
        salida = BytesIO()
        Image.new('RGB', (320, 200), '#ffffff').save(salida, 'PNG')
        reporte, _ = self.reportar(archivo=SimpleUploadedFile('sintetico.png', salida.getvalue()))
        self.assertEqual(reporte.comprobante.content_type, 'image/png')
        self.client.force_login(self.pagador)
        self.assertContains(self.client.get(reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, reporte.pk])), 'pendiente de verificaci')

    @patch('financiacion_educativa.management.commands.procesar_comprobantes_educativos.close_old_connections')
    def test_comando_agotados_no_bloquean_documentos_nuevos(self, _limpieza_del_worker):
        reporte, _ = self.reportar()
        with override_settings(FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=1):
            self.escanear(reporte.comprobante_id, Fallo())
            nuevo, _ = self.reportar()
            with patch('financiacion_educativa.services.recaudos.obtener_backend_escaneo', return_value=Limpio()):
                call_command('procesar_comprobantes_educativos', once=True, limit=1, stdout=StringIO())
        self.assertEqual(reporte.comprobante.estado_escaneo, 'ERROR')
        self.assertEqual(nuevo.comprobante.estado_escaneo, 'SAFE')

    def test_receptor_admin_creacion_y_canonizacion_de_cuenta(self):
        self.client.force_login(self.actor)
        url = reverse('admin:financiacion_educativa_receptorbrebeducativo_add')
        respuesta = self.client.post(url, dict(entidad='banco sintetico', identificador_cuenta='cuenta sintetica',
            titular='RECEPTOR VERSION DOS', llave='NUEVA-SINTETICA', qr_subido=imagen_jpeg_prueba(), _save='Guardar'))
        self.assertEqual(respuesta.status_code, 302)
        nuevo = ReceptorBrebEducativo.objects.exclude(pk=self.receptor.pk).get()
        self.assertEqual(nuevo.cuenta_id, self.receptor.cuenta_id)
        self.assertEqual(nuevo.qr.estado_escaneo, 'PENDING')
        request = RequestFactory().get('/')
        request.user = self.actor
        self.assertIn('activar', admin.site._registry[ReceptorBrebEducativo].get_actions(request))
        self.assertEqual(self.client.get(reverse('admin:financiacion_educativa_archivorecaudoeducativo_change', args=[nuevo.qr_id])).status_code, 200)

    def test_plantilla_worker_protegida_y_continua(self):
        from pathlib import Path
        unidad = (Path(__file__).resolve().parents[2] / 'deploy/systemd/fundetec-staging-receipts-scan.service').read_text()
        for requisito in ('User=fundetec-staging', 'Group=fundetec-staging', 'ProtectSystem=strict',
            'NoNewPrivileges=true', 'PrivateTmp=true', 'ProtectHome=true', 'Restart=on-failure',
            'WorkingDirectory=/var/www/fundetec-staging/current', 'EnvironmentFile=/var/www/fundetec-staging/shared/staging.env',
            'ExecStart=/var/www/fundetec-staging/shared/venv/bin/python manage.py procesar_comprobantes_educativos'):
            self.assertIn(requisito, unidad)
        self.assertNotIn('--once', unidad)

    def test_limpieza_qa_reconoce_y_preserva_recaudos(self):
        from financiacion_educativa.services.limpieza_solicitudes import construir_plan_limpieza, ejecutar_limpieza_solicitudes, ErrorLimpiezaSolicitudes
        self.reportar()
        plan = construir_plan_limpieza(self.institucion)
        self.assertEqual(dict(plan.conteos)['ReporteRecaudoEducativo'], 1)
        with self.assertRaisesMessage(ErrorLimpiezaSolicitudes, 'relacion protegida'):
            ejecutar_limpieza_solicitudes(institucion_id=self.institucion.pk, expected_count=1)
        self.assertEqual(ReporteRecaudoEducativo.objects.count(), 1)


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real aislado.')
@override_settings(**AISLAMIENTO)
class ConcurrenciaRecaudosTests(DatosRecaudosMixin, TransactionTestCase):
    def setUp(self):
        self.preparar_recaudos()

    def paralelos(self, operaciones):
        barrera = threading.Barrier(2)
        resultados, errores = [], []
        principal = connection.connection

        def ejecutar(operacion):
            close_old_connections()
            propia = connections['default']
            try:
                barrera.wait(timeout=10)
                resultados.append(operacion())
            except Exception as exc:
                errores.append(exc)
            finally:
                propia.close()

        hilos = [threading.Thread(target=ejecutar, args=[operacion]) for operacion in operaciones]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=20)
            self.assertFalse(hilo.is_alive(), 'El hilo no termino.')
        self.assertIs(connection.connection, principal)
        self.assertTrue(connection.is_usable())
        self.assertTrue(ReporteRecaudoEducativo.objects.count() >= 0)
        return resultados, errores

    def test_dos_confirmaciones_mismo_reporte_crean_una_conciliacion(self):
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte)

        def confirmar():
            actor = get_user_model().objects.get(pk=self.actor.pk)
            return servicios.confirmar_recaudo(**{**datos, 'actor': actor})

        resultados, errores = self.paralelos([confirmar, confirmar])
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        self.assertEqual({resultado[0].pk for resultado in resultados}.__len__(), 1)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_dos_reportes_compiten_por_un_movimiento_global(self):
        primero, _ = self.reportar()
        segundo, _ = self.reportar()
        for reporte in (primero, segundo):
            self.escanear(reporte.comprobante_id)
        evidencia = self.evidencia()
        datos = [self.datos_confirmacion(reporte, evidencia) for reporte in (primero, segundo)]

        def operacion(indice):
            actor = get_user_model().objects.get(pk=self.actor.pk)
            return servicios.confirmar_recaudo(**{**datos[indice], 'actor': actor})

        resultados, errores = self.paralelos([lambda: operacion(0), lambda: operacion(1)])
        self.assertEqual(len(resultados), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], ValidationError)
        self.assertEqual(ConciliacionRecaudoEducativo.objects.count(), 1)

    def test_dos_escaneos_crean_un_solo_intento_limpio(self):
        reporte, _ = self.reportar()
        resultados, errores = self.paralelos([
            lambda: self.escanear(reporte.comprobante_id), lambda: self.escanear(reporte.comprobante_id),
        ])
        self.assertEqual(errores, [])
        self.assertEqual(sum(resultado is not None for resultado in resultados), 1)
        self.assertEqual(reporte.comprobante.escaneos.count(), 1)
