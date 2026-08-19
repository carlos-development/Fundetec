from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoValidacionIADocumento,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    OrigenValidacionIADocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    DecisionRevisionDocumentoOperativa,
    DecisionRevisionEducativa,
    DocumentoFinanciacion,
    OutboxCorreoEducativo,
    ProcesoAutomatizacionEducativa,
    ValidacionIADocumento,
)
from financiacion_educativa.dashboards.operaciones.views import (
    previsualizar_documento_operativo_view,
)
from financiacion_educativa.services.documentos import revisar_documento
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_helpers import registrar_resultado_escaneo
from financiacion_educativa.services.revision_documental_operativa import (
    aceptar_documento_operativo,
    solicitar_correccion_documento_operativo,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_QA_MODE=False,
    EMAIL_LIVE_DELIVERY_ENABLED=False,
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
)
class DashboardOperativoRevisionDocumentalTests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.addCleanup(self.private_root.cleanup)
        self.private_settings = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.private_settings.enable()
        self.addCleanup(self.private_settings.disable)
        User = get_user_model()
        self.revisor = User.objects.create_user(
            username='revisor-doc@example.test',
            email='revisor-doc@example.test',
            password='Clave-2026',
        )
        self.auditor = User.objects.create_user(
            username='auditor-doc@example.test',
            email='auditor-doc@example.test',
            password='Clave-2026',
        )
        self.staff = User.objects.create_user(
            username='staff-doc@example.test',
            password='Clave-2026',
            is_staff=True,
        )
        self.propietario = User.objects.create_user(
            username='persona-doc@example.test',
            email='persona-doc@example.test',
            password='Clave-2026',
        )
        for usuario in (self.revisor, self.auditor):
            self._permisos(
                usuario,
                'acceder_dashboard_operativo',
                'consultar_solicitudes_operativas',
                'consultar_documentos_validaciones_operativas',
                'acceder_revision_documental_operativa',
            )
        self._permisos(
            self.revisor,
            'decidir_revision_documental_operativa',
            'escanear_documento_financiacion',
        )
        self.solicitud = crear_solicitud(
            usuario=self.propietario,
            correo='persona-doc@example.test',
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.documento = DocumentoFinanciacion.objects.create(
            solicitud=self.solicitud,
            participante=None,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=SimpleUploadedFile(
                'ingresos.pdf',
                b'%PDF-1.4\ncontenido-prueba',
                content_type='application/pdf',
            ),
            nombre_original='ingresos.pdf',
            content_type='application/pdf',
            cargado_por=self.propietario,
        )
        registrar_resultado_escaneo(
            documento=self.documento,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='revision-dashboard-clean',
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        self.solicitud.save(update_fields=['estado'])
        self.lista = reverse(
            'financiacion_educativa_web:operaciones:revision-documental'
        )
        self.detalle = reverse(
            'financiacion_educativa_web:operaciones:revision-documento',
            args=[self.documento.pk],
        )

    @staticmethod
    def _permisos(usuario, *codenames):
        usuario.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='financiacion_educativa',
                codename__in=codenames,
            )
        )

    def _crear_documento(
        self,
        sufijo,
        *,
        estado_solicitud=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        estado_escaneo=EstadoEscaneoDocumento.SAFE,
        estado_validacion=EstadoValidacionDocumento.PENDING,
        activo=True,
        reemplaza_a=None,
    ):
        solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia=f'REV-DOC-{sufijo}',
            usuario=self.propietario,
            correo=f'revision-{sufijo}@example.test',
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        documento = DocumentoFinanciacion.objects.create(
            solicitud=solicitud,
            participante=None,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=SimpleUploadedFile(
                f'ingresos-{sufijo}.pdf',
                f'%PDF-1.4\ncontenido-{sufijo}'.encode(),
                content_type='application/pdf',
            ),
            nombre_original=f'ingresos-{sufijo}.pdf',
            content_type='application/pdf',
            cargado_por=self.propietario,
            reemplaza_a=reemplaza_a,
        )
        if estado_escaneo in {
            EstadoEscaneoDocumento.SAFE,
            EstadoEscaneoDocumento.BLOCKED,
        }:
            registrar_resultado_escaneo(
                documento=documento,
                actor=self.revisor,
                estado=estado_escaneo,
                referencia_escaneo=f'revision-{sufijo}',
            )
        if estado_validacion != EstadoValidacionDocumento.PENDING:
            revisar_documento(
                documento=documento,
                actor=self.revisor,
                aceptar=(
                    estado_validacion == EstadoValidacionDocumento.APPROVED
                ),
                motivo_rechazo=(
                    ''
                    if estado_validacion == EstadoValidacionDocumento.APPROVED
                    else MotivoRechazoDocumento.UNREADABLE
                ),
                observacion='Preparacion controlada de la prueba.',
            )
        solicitud.estado = estado_solicitud
        solicitud.save(update_fields=['estado'])
        if not activo:
            documento.activo = False
            documento.save(update_fields=['activo', 'actualizado_en'])
        documento.refresh_from_db()
        return solicitud, documento

    @staticmethod
    def _estado_sin_efectos(documento):
        documento.refresh_from_db()
        documento.solicitud.refresh_from_db()
        return {
            'documento': (
                documento.activo,
                documento.estado_escaneo,
                documento.estado_validacion,
                documento.motivo_rechazo,
                documento.observacion_revision,
            ),
            'solicitud': documento.solicitud.estado,
            'decisiones_documento': (
                DecisionRevisionDocumentoOperativa.objects.count()
            ),
            'decisiones_solicitud': DecisionRevisionEducativa.objects.count(),
            'outbox': OutboxCorreoEducativo.objects.count(),
            'procesos': ProcesoAutomatizacionEducativa.objects.count(),
        }

    def _afirmar_decisiones_invalidas_sin_efectos(self, documento):
        urls_datos = (
            (
                reverse(
                    'financiacion_educativa_web:operaciones:documento-aceptar',
                    args=[documento.pk],
                ),
                {'observacion': 'Revision controlada.'},
            ),
            (
                reverse(
                    'financiacion_educativa_web:operaciones:documento-solicitar-correccion',
                    args=[documento.pk],
                ),
                {
                    'motivo': MotivoRechazoDocumento.UNREADABLE,
                    'mensaje_solicitante': 'Carga una copia legible.',
                    'nota_interna': 'Revision controlada.',
                },
            ),
        )
        for url, datos in urls_datos:
            antes = self._estado_sin_efectos(documento)
            respuesta = self.client.post(url, datos)
            self.assertEqual(respuesta.status_code, 302)
            self.assertNotIn(b'Traceback', respuesta.content)
            self.assertEqual(self._estado_sin_efectos(documento), antes)

    def test_autorizacion_diferencia_consulta_y_decision(self):
        self.assertEqual(self.client.get(self.lista).status_code, 302)
        for usuario, esperado in (
            (self.staff, 403),
            (self.propietario, 403),
            (self.auditor, 200),
            (self.revisor, 200),
        ):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(self.lista).status_code, esperado)
        self.client.force_login(self.auditor)
        self.assertNotContains(self.client.get(self.detalle), 'Aceptar documento')

    def test_bandeja_incluye_solo_documento_vigente_pendiente(self):
        self.client.force_login(self.revisor)
        respuesta = self.client.get(self.lista)
        self.assertContains(respuesta, self.solicitud.referencia_externa)
        self.documento.activo = False
        self.documento.save(update_fields=['activo', 'actualizado_en'])
        self.assertNotContains(
            self.client.get(self.lista),
            self.solicitud.referencia_externa,
        )

    def test_bandeja_pagina_26_documentos_y_conserva_filtros(self):
        for indice in range(25):
            self._crear_documento(f'paginacion-{indice:02d}')
        self.client.force_login(self.revisor)
        parametros = (
            f'tipo={TipoDocumentoFinanciacion.INCOME_CERTIFICATE}'
            '&orden=cargado_en'
        )
        primera = self.client.get(f'{self.lista}?{parametros}')
        segunda = self.client.get(f'{self.lista}?{parametros}&page=2')
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(primera.context['pagina'].paginator.count, 26)
        self.assertEqual(len(primera.context['pagina'].object_list), 25)
        self.assertEqual(primera.context['pagina'].number, 1)
        self.assertContains(
            primera,
            f'tipo={TipoDocumentoFinanciacion.INCOME_CERTIFICATE}'
            '&amp;orden=cargado_en&amp;page=2',
        )
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.context['pagina'].paginator.count, 26)
        self.assertEqual(len(segunda.context['pagina'].object_list), 1)
        self.assertEqual(segunda.context['pagina'].number, 2)

    def test_bandeja_excluye_documentos_no_elegibles_y_no_duplica_por_historial(self):
        _, inactivo = self._crear_documento('inactivo', activo=False)
        solicitud_reemplazo, reemplazado = self._crear_documento('reemplazado')
        reemplazado.activo = False
        reemplazado.save(update_fields=['activo', 'actualizado_en'])
        reemplazo = DocumentoFinanciacion.objects.create(
            solicitud=solicitud_reemplazo,
            participante=None,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=SimpleUploadedFile(
                'reemplazo.pdf',
                b'%PDF-1.4\nreemplazo',
                content_type='application/pdf',
            ),
            nombre_original='reemplazo.pdf',
            content_type='application/pdf',
            cargado_por=self.propietario,
            reemplaza_a=reemplazado,
        )
        registrar_resultado_escaneo(
            documento=reemplazo,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
        )
        solicitud_reemplazo.estado = (
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        )
        solicitud_reemplazo.save(update_fields=['estado'])
        _, aceptado = self._crear_documento(
            'aceptado',
            estado_validacion=EstadoValidacionDocumento.APPROVED,
        )
        _, cerrado = self._crear_documento(
            'cerrado',
            estado_solicitud=EstadoSolicitudFinanciacion.APPROVED,
        )
        _, inseguro = self._crear_documento(
            'inseguro',
            estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )
        _, no_manual = self._crear_documento(
            'no-manual',
            estado_solicitud=EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        )
        for numero in (1, 2):
            ValidacionIADocumento.objects.create(
                documento=self.documento,
                numero=numero,
                estado=EstadoValidacionIADocumento.MANUAL_REVIEW,
                origen=OrigenValidacionIADocumento.AUTOMATIC,
                proveedor='proveedor-prueba',
                modelo='modelo-prueba',
                calidad=Decimal('0.7000'),
                legibilidad=Decimal('0.8000'),
                confianza=Decimal('0.7000'),
                corresponde_tipo=True,
                hallazgos=[f'HISTORICO_{numero}'],
            )
        self.client.force_login(self.revisor)
        respuesta = self.client.get(self.lista)
        contenido = respuesta.content.decode()
        self.assertEqual(respuesta.context['pagina'].paginator.count, 2)
        self.assertIn(str(self.documento.pk), contenido)
        self.assertIn(str(reemplazo.pk), contenido)
        for excluido in (
            inactivo,
            reemplazado,
            aceptado,
            cerrado,
            inseguro,
            no_manual,
        ):
            self.assertNotIn(str(excluido.pk), contenido)
        self.assertEqual(contenido.count(str(self.documento.pk)), 1)

    def test_bandeja_mantiene_presupuesto_estable_de_consultas(self):
        for indice in range(25):
            self._crear_documento(f'queries-{indice:02d}')
        self.client.force_login(self.revisor)
        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.client.get(self.lista)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['pagina'].paginator.count, 26)
        self.assertLessEqual(
            len(consultas),
            12,
            f'La bandeja ejecuto {len(consultas)} consultas; posible N+1.',
        )

    def test_previsualizacion_privada_aplica_headers_y_no_filtra_ruta(self):
        self.client.force_login(self.auditor)
        conexion_principal = connections['default']
        self.assertIsNotNone(conexion_principal.connection)
        conexion_db_principal = conexion_principal.connection
        url = reverse(
            'financiacion_educativa_web:operaciones:documento-previsualizar',
            args=[self.documento.pk],
        )
        request = RequestFactory().get(url)
        request.user = self.auditor
        respuesta = previsualizar_documento_operativo_view(
            request,
            application_id=self.documento.pk,
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertNotIn(self.documento.archivo.name, str(respuesta.headers))
        contenido = b''.join(respuesta.streaming_content)
        self.assertTrue(contenido.startswith(b'%PDF-1.4'))
        archivo_respuesta = respuesta.file_to_stream
        self.assertFalse(archivo_respuesta.closed)
        archivo_respuesta.close()
        self.assertTrue(archivo_respuesta.closed)
        self.assertIs(conexion_principal.connection, conexion_db_principal)
        self.assertTrue(conexion_principal.is_usable())
        self.assertTrue(
            DocumentoFinanciacion.objects.filter(pk=self.documento.pk).exists()
        )
        self.documento.content_type = 'text/html'
        self.documento.save(update_fields=['content_type', 'actualizado_en'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_aceptacion_es_auditada_idempotente_y_no_ejecuta_proveedor(self):
        self.client.force_login(self.revisor)
        url = reverse(
            'financiacion_educativa_web:operaciones:documento-aceptar',
            args=[self.documento.pk],
        )
        with mock.patch(
            'financiacion_educativa.services.revision_documental_operativa.programar_orquestacion_automatica'
        ) as programar:
            primera = self.client.post(url, {'observacion': 'Verificado.'})
            segunda = self.client.post(url, {'observacion': 'Verificado.'})
            diferente = self.client.post(url, {'observacion': 'Otra decision.'})
        self.documento.refresh_from_db()
        self.assertEqual(primera.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(diferente.status_code, 302)
        self.assertEqual(
            self.documento.estado_validacion,
            EstadoValidacionDocumento.APPROVED,
        )
        self.assertEqual(DecisionRevisionDocumentoOperativa.objects.count(), 1)
        programar.assert_not_called()

    def test_correccion_crea_auditoria_y_outbox_sin_enviar_correo(self):
        self.client.force_login(self.revisor)
        url = reverse(
            'financiacion_educativa_web:operaciones:documento-solicitar-correccion',
            args=[self.documento.pk],
        )
        respuesta = self.client.post(url, {
            'motivo': MotivoRechazoDocumento.UNREADABLE,
            'mensaje_solicitante': 'Carga una copia legible.',
            'nota_interna': 'Control interno.',
        })
        self.documento.refresh_from_db()
        self.solicitud.refresh_from_db()
        decision = DecisionRevisionDocumentoOperativa.objects.get()
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        )
        self.assertEqual(
            self.documento.estado_validacion,
            EstadoValidacionDocumento.REJECTED,
        )
        self.assertEqual(decision.nota_interna, 'Control interno.')
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_decisiones_solo_post_y_csrf(self):
        self.client.force_login(self.revisor)
        url = reverse(
            'financiacion_educativa_web:operaciones:documento-aceptar',
            args=[self.documento.pk],
        )
        self.assertEqual(self.client.get(url).status_code, 405)
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.revisor)
        self.assertEqual(cliente.post(url).status_code, 403)
        with self.assertRaisesMessage(
            ValidationError,
            'No tienes permiso para decidir revisiones documentales.',
        ):
            aceptar_documento_operativo(
                documento_id=self.documento.pk,
                actor=self.auditor,
            )

    def test_documento_inactivo_e_inexistente_responden_404(self):
        self.client.force_login(self.revisor)
        self.documento.activo = False
        self.documento.save(update_fields=['activo', 'actualizado_en'])
        self.assertEqual(self.client.get(self.detalle).status_code, 404)

    def test_decisiones_invalidas_no_dejan_efectos_parciales(self):
        self.client.force_login(self.revisor)
        _, inactivo = self._crear_documento('decision-inactivo', activo=False)
        solicitud_reemplazo, reemplazado = self._crear_documento(
            'decision-reemplazado'
        )
        reemplazado.activo = False
        reemplazado.save(update_fields=['activo', 'actualizado_en'])
        reemplazo = DocumentoFinanciacion.objects.create(
            solicitud=solicitud_reemplazo,
            participante=None,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=SimpleUploadedFile(
                'decision-reemplazo.pdf',
                b'%PDF-1.4\ndecision-reemplazo',
                content_type='application/pdf',
            ),
            nombre_original='decision-reemplazo.pdf',
            content_type='application/pdf',
            cargado_por=self.propietario,
            reemplaza_a=reemplazado,
        )
        registrar_resultado_escaneo(
            documento=reemplazo,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
        )
        _, resuelto = self._crear_documento(
            'decision-resuelto',
            estado_validacion=EstadoValidacionDocumento.APPROVED,
        )
        _, inseguro = self._crear_documento(
            'decision-inseguro',
            estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )
        _, cerrado = self._crear_documento(
            'decision-cerrado',
            estado_solicitud=EstadoSolicitudFinanciacion.APPROVED,
        )
        _, incompatible = self._crear_documento(
            'decision-incompatible',
            estado_solicitud=EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        )
        for documento in (
            inactivo,
            reemplazado,
            resuelto,
            inseguro,
            cerrado,
            incompatible,
        ):
            with self.subTest(documento=documento.pk):
                self._afirmar_decisiones_invalidas_sin_efectos(documento)

    def test_formulario_correccion_invalido_no_deja_efectos(self):
        self.client.force_login(self.revisor)
        url = reverse(
            'financiacion_educativa_web:operaciones:documento-solicitar-correccion',
            args=[self.documento.pk],
        )
        for datos in (
            {
                'motivo': 'MOTIVO_INEXISTENTE',
                'mensaje_solicitante': 'Carga una copia legible.',
            },
            {
                'motivo': MotivoRechazoDocumento.UNREADABLE,
                'mensaje_solicitante': '   ',
            },
        ):
            with self.subTest(datos=datos):
                antes = self._estado_sin_efectos(self.documento)
                respuesta = self.client.post(url, datos)
                self.assertEqual(respuesta.status_code, 302)
                self.assertNotIn(b'Traceback', respuesta.content)
                self.assertEqual(
                    self._estado_sin_efectos(self.documento),
                    antes,
                )

    def test_usuario_solo_consulta_no_puede_decidir_ni_crea_efectos(self):
        self.client.force_login(self.auditor)
        urls_datos = (
            (
                reverse(
                    'financiacion_educativa_web:operaciones:documento-aceptar',
                    args=[self.documento.pk],
                ),
                {'observacion': 'No autorizado.'},
            ),
            (
                reverse(
                    'financiacion_educativa_web:operaciones:documento-solicitar-correccion',
                    args=[self.documento.pk],
                ),
                {
                    'motivo': MotivoRechazoDocumento.UNREADABLE,
                    'mensaje_solicitante': 'No autorizado.',
                },
            ),
        )
        for url, datos in urls_datos:
            antes = self._estado_sin_efectos(self.documento)
            respuesta = self.client.post(url, datos)
            self.assertEqual(respuesta.status_code, 403)
            self.assertEqual(self._estado_sin_efectos(self.documento), antes)

    def test_correccion_repetida_es_idempotente_y_no_duplica_outbox(self):
        datos = {
            'motivo': MotivoRechazoDocumento.UNREADABLE,
            'mensaje_solicitante': 'Carga una copia legible.',
            'nota_interna': 'Misma evaluacion.',
        }
        primera = solicitar_correccion_documento_operativo(
            documento_id=self.documento.pk,
            actor=self.revisor,
            **datos,
        )
        segunda = solicitar_correccion_documento_operativo(
            documento_id=self.documento.pk,
            actor=self.revisor,
            **datos,
        )
        self.assertFalse(primera.repetida)
        self.assertTrue(segunda.repetida)
        self.assertEqual(primera.decision.pk, segunda.decision.pk)
        self.assertEqual(DecisionRevisionDocumentoOperativa.objects.count(), 1)
        self.assertEqual(DecisionRevisionEducativa.objects.count(), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)
        self.assertEqual(ProcesoAutomatizacionEducativa.objects.count(), 0)

    def test_decisiones_incompatibles_producen_conflicto_sin_duplicados(self):
        aceptar_documento_operativo(
            documento_id=self.documento.pk,
            actor=self.revisor,
            observacion='Aceptado.',
        )
        with self.assertRaises(ValidationError):
            solicitar_correccion_documento_operativo(
                documento_id=self.documento.pk,
                actor=self.revisor,
                motivo=MotivoRechazoDocumento.UNREADABLE,
                mensaje_solicitante='Carga una copia legible.',
            )
        _, documento_corregido = self._crear_documento(
            'conflicto-correccion-aceptacion'
        )
        solicitar_correccion_documento_operativo(
            documento_id=documento_corregido.pk,
            actor=self.revisor,
            motivo=MotivoRechazoDocumento.UNREADABLE,
            mensaje_solicitante='Carga una copia legible.',
        )
        with self.assertRaises(ValidationError):
            aceptar_documento_operativo(
                documento_id=documento_corregido.pk,
                actor=self.revisor,
                observacion='Aceptado despues de correccion.',
            )
        self.assertEqual(DecisionRevisionDocumentoOperativa.objects.count(), 2)
        self.assertEqual(DecisionRevisionEducativa.objects.count(), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)
        self.assertEqual(ProcesoAutomatizacionEducativa.objects.count(), 0)

    def test_decision_no_modifica_validaciones_ia_historicas(self):
        intento = ValidacionIADocumento.objects.create(
            documento=self.documento,
            numero=1,
            estado=EstadoValidacionIADocumento.MANUAL_REVIEW,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
            proveedor='proveedor-historico',
            modelo='modelo-historico',
            calidad=Decimal('0.7100'),
            legibilidad=Decimal('0.8200'),
            confianza=Decimal('0.7300'),
            corresponde_tipo=None,
            hallazgos=['INCONCLUSIVE'],
            resultado_estructurado={'version': 'historica'},
        )
        antes = ValidacionIADocumento.objects.filter(pk=intento.pk).values().get()
        aceptar_documento_operativo(
            documento_id=self.documento.pk,
            actor=self.revisor,
            observacion='Revision humana.',
        )
        despues = ValidacionIADocumento.objects.filter(pk=intento.pk).values().get()
        self.assertEqual(despues, antes)


@skipUnless(
    connection.vendor == 'postgresql',
    'Requiere PostgreSQL real para validar select_for_update entre conexiones.',
)
@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_QA_MODE=False,
    EMAIL_LIVE_DELIVERY_ENABLED=False,
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
)
class RevisionDocumentalConcurrentePostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.addCleanup(self.private_root.cleanup)
        self.private_settings = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.private_settings.enable()
        self.addCleanup(self.private_settings.disable)
        User = get_user_model()
        self.propietario = User.objects.create_user(
            username='propietario-concurrencia@example.test',
            email='propietario-concurrencia@example.test',
            password='Clave-2026',
        )
        self.revisores = [
            User.objects.create_user(
                username=f'revisor-concurrencia-{indice}@example.test',
                email=f'revisor-concurrencia-{indice}@example.test',
                password='Clave-2026',
            )
            for indice in (1, 2)
        ]
        permisos = Permission.objects.filter(
            content_type__app_label='financiacion_educativa',
            codename__in={
                'decidir_revision_documental_operativa',
                'escanear_documento_financiacion',
            },
        )
        for revisor in self.revisores:
            revisor.user_permissions.add(*permisos)
        self.solicitud = crear_solicitud(
            referencia='REV-CONCURRENTE',
            usuario=self.propietario,
            correo='propietario-concurrencia@example.test',
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.documento = DocumentoFinanciacion.objects.create(
            solicitud=self.solicitud,
            participante=None,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=SimpleUploadedFile(
                'concurrencia.pdf',
                b'%PDF-1.4\nconcurrencia',
                content_type='application/pdf',
            ),
            nombre_original='concurrencia.pdf',
            content_type='application/pdf',
            cargado_por=self.propietario,
        )
        registrar_resultado_escaneo(
            documento=self.documento,
            actor=self.revisores[0],
            estado=EstadoEscaneoDocumento.SAFE,
        )
        self.solicitud.estado = (
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        )
        self.solicitud.save(update_fields=['estado'])

    def _aceptar_desde_conexion_independiente(self, actor_id, barrera):
        close_old_connections()
        conexion_thread = connections['default']
        try:
            conexion_thread.ensure_connection()
            wrapper_id = id(conexion_thread)
            conexion_db_id = id(conexion_thread.connection)
            actor = get_user_model().objects.get(pk=actor_id)
            barrera.wait(timeout=10)
            resultado = aceptar_documento_operativo(
                documento_id=self.documento.pk,
                actor=actor,
                observacion='Decision concurrente identica.',
            )
            return (
                'ok',
                resultado.repetida,
                str(resultado.decision.pk),
                wrapper_id,
                conexion_db_id,
            )
        except Exception as error:  # El test debe hacer visibles fallos del hilo.
            return ('error', type(error).__name__, str(error))
        finally:
            conexion_thread.close()

    def test_dos_revisores_generan_una_sola_decision_efectiva(self):
        conexion_principal = connections['default']
        conexion_principal.ensure_connection()
        wrapper_principal_id = id(conexion_principal)
        conexion_db_principal = conexion_principal.connection
        conexion_db_principal_id = id(conexion_db_principal)
        barrera = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            futuros = [
                ejecutor.submit(
                    self._aceptar_desde_conexion_independiente,
                    revisor.pk,
                    barrera,
                )
                for revisor in self.revisores
            ]
            resultados = [futuro.result(timeout=20) for futuro in futuros]
        errores = [resultado for resultado in resultados if resultado[0] == 'error']
        self.assertEqual(errores, [], resultados)
        self.assertCountEqual(
            [resultado[1] for resultado in resultados],
            [False, True],
        )
        self.assertEqual(
            len({resultado[2] for resultado in resultados}),
            1,
        )
        self.assertEqual(len({resultado[3] for resultado in resultados}), 2)
        self.assertEqual(len({resultado[4] for resultado in resultados}), 2)
        self.assertNotIn(
            wrapper_principal_id,
            {resultado[3] for resultado in resultados},
        )
        self.assertNotIn(
            conexion_db_principal_id,
            {resultado[4] for resultado in resultados},
        )
        self.assertIs(conexion_principal.connection, conexion_db_principal)
        self.assertTrue(conexion_principal.is_usable())
        self.assertTrue(
            DocumentoFinanciacion.objects.filter(pk=self.documento.pk).exists()
        )
        self.documento.refresh_from_db()
        self.assertEqual(
            self.documento.estado_validacion,
            EstadoValidacionDocumento.APPROVED,
        )
        self.assertEqual(DecisionRevisionDocumentoOperativa.objects.count(), 1)
        self.assertEqual(DecisionRevisionEducativa.objects.count(), 0)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 0)
        self.assertEqual(ProcesoAutomatizacionEducativa.objects.count(), 0)
