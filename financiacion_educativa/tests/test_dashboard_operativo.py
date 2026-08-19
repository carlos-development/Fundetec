from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    CodigoMensajeCorreoEducativo,
    CodigoRazonAutomatizacionEducativa,
    EstadoEscaneoDocumento,
    EstadoOutboxCorreoEducativo,
    EstadoProcesoAutomatizacionEducativa,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    EstadoValidacionIADocumento,
    EtapaAutomatizacionEducativa,
    OrigenCapturaDocumento,
    OrigenValidacionIADocumento,
    TipoDocumentoFinanciacion,
    TipoEventoCorreoEducativo,
)
from financiacion_educativa.dashboards.operaciones.permissions import (
    PERMISO_ACCESO,
    PERMISO_DATOS_INTEGRALES,
    PERMISO_DOCUMENTOS,
    PERMISO_PROCESOS,
    PERMISO_SOLICITUDES,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    OutboxCorreoEducativo,
    ProcesoAutomatizacionEducativa,
    ValidacionIADocumento,
)
from financiacion_educativa.tests.factories import crear_solicitud
from instituciones.models import Institucion, MembresiaInstitucion
from instituciones.services.membresias import crear_membresia


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_QA_MODE=False,
    EMAIL_LIVE_DELIVERY_ENABLED=False,
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
)
class DashboardOperativoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.operador = User.objects.create_user(
            username='operador@example.test',
            email='operador@example.test',
            password='Clave-2026',
        )
        self.sin_permisos = User.objects.create_user(
            username='sin-permisos@example.test',
            email='sin-permisos@example.test',
            password='Clave-2026',
        )
        self.staff_sin_permisos = User.objects.create_user(
            username='staff@example.test',
            email='staff@example.test',
            password='Clave-2026',
            is_staff=True,
        )
        self.institucional = User.objects.create_user(
            username='institucional@example.test',
            email='institucional@example.test',
            password='Clave-2026',
        )
        self.institucion = self._institucion('Principal', '901800001')
        self.otra_institucion = self._institucion('Secundaria', '901800002')
        crear_membresia(
            usuario=self.institucional,
            institucion=self.institucion,
            rol=MembresiaInstitucion.Rol.INSTITUTION_ADMIN,
            actor=self.operador,
        )
        self.inicio = reverse(
            'financiacion_educativa_web:operaciones:inicio'
        )
        self.listado = reverse(
            'financiacion_educativa_web:operaciones:solicitudes'
        )
        self.bandejas = reverse(
            'financiacion_educativa_web:operaciones:bandejas'
        )
        self.instituciones = reverse(
            'financiacion_educativa_web:operaciones:instituciones'
        )

    @staticmethod
    def _institucion(nombre, nit):
        return Institucion.objects.create(
            nombre_comercial=f'Instituto {nombre}',
            razon_social=f'Instituto {nombre} SAS',
            numero_identificacion_tributaria=nit,
        )

    @staticmethod
    def _permiso(nombre):
        codename = nombre.split('.', 1)[1]
        return Permission.objects.get(
            content_type__app_label='financiacion_educativa',
            codename=codename,
        )

    def _autorizar(self, usuario=None, *permisos):
        usuario = usuario or self.operador
        usuario.user_permissions.add(
            *(self._permiso(permiso) for permiso in permisos)
        )
        return usuario

    def _autorizar_consulta_completa(self, usuario=None):
        return self._autorizar(
            usuario,
            PERMISO_ACCESO,
            PERMISO_SOLICITUDES,
            PERMISO_DOCUMENTOS,
            PERMISO_PROCESOS,
            PERMISO_DATOS_INTEGRALES,
        )

    def _solicitud(self, referencia, *, institucion=None, estado=None, **campos):
        solicitud = crear_solicitud(
            institucion=institucion or self.institucion,
            referencia=referencia,
            correo=campos.pop('correo', f'{referencia.lower()}@example.test'),
        )
        for campo, valor in campos.items():
            setattr(solicitud, campo, valor)
        if estado:
            solicitud.estado = estado
        solicitud.save()
        return solicitud

    def _detalle(self, solicitud):
        return reverse(
            'financiacion_educativa_web:operaciones:solicitud-detalle',
            kwargs={'application_id': solicitud.pk},
        )

    @staticmethod
    def _proceso(solicitud, *, estado, razon='', version=1):
        return ProcesoAutomatizacionEducativa.objects.create(
            solicitud=solicitud,
            version_expediente=version,
            estado=estado,
            etapa_actual=EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
            codigo_razon=razon,
            proxima_ejecucion_en=timezone.now(),
        )

    @staticmethod
    def _correo(solicitud, estado, sufijo):
        return OutboxCorreoEducativo.objects.create(
            solicitud=solicitud,
            tipo_evento=TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
            clave_idempotencia=f'clave-{sufijo}',
            evento_logico=(sufijo[0] * 64),
            destinatarios=['persona-privada@example.test'],
            destinatarios_copia=['copia-privada@example.test'],
            codigo_mensaje=CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
            contexto={'token': 'CONTEXTO_SECRETO'},
            estado=estado,
            message_id=f'message-{sufijo}',
        )

    def test_rutas_operativas_son_estables(self):
        self.assertEqual(
            self.inicio,
            '/financiacion-educativa/operaciones/',
        )
        self.assertEqual(
            self.listado,
            '/financiacion-educativa/operaciones/solicitudes/',
        )

    def test_anonimo_es_redirigido_y_usuarios_no_autorizados_reciben_403(self):
        self.assertEqual(self.client.get(self.inicio).status_code, 302)
        for usuario in (
            self.sin_permisos,
            self.staff_sin_permisos,
            self.institucional,
        ):
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.inicio).status_code, 403)

    def test_permiso_de_acceso_sin_consulta_no_es_suficiente(self):
        self._autorizar(self.operador, PERMISO_ACCESO)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(self.inicio).status_code, 403)

    def test_operador_autorizado_y_superusuario_acceden(self):
        self._autorizar_consulta_completa()
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(self.inicio).status_code, 200)

        superusuario = get_user_model().objects.create_superuser(
            username='super@example.test',
            email='super@example.test',
            password='Clave-2026',
        )
        self.client.force_login(superusuario)
        self.assertEqual(self.client.get(self.inicio).status_code, 200)

    def test_indicadores_son_globales_y_no_duplican_instituciones(self):
        self._autorizar_consulta_completa()
        propia = self._solicitud(
            'GLOBAL-UNO',
            estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
            valor_plan=Decimal('300000.00'),
        )
        self._solicitud(
            'GLOBAL-DOS',
            institucion=self.otra_institucion,
            estado=EstadoSolicitudFinanciacion.APPROVED,
            valor_plan=Decimal('700000.00'),
        )
        self._proceso(
            propia,
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            razon=(
                CodigoRazonAutomatizacionEducativa.DOCUMENT_VALIDATION_INCONCLUSIVE
            ),
        )
        self._correo(
            propia,
            EstadoOutboxCorreoEducativo.PENDING,
            'p' + uuid4().hex,
        )
        self.client.force_login(self.operador)

        respuesta = self.client.get(self.inicio)
        indicadores = respuesta.context['indicadores']

        self.assertEqual(indicadores['total'], 2)
        self.assertEqual(indicadores['revision_manual'], 1)
        self.assertEqual(indicadores['aprobadas'], 1)
        self.assertEqual(indicadores['procesos_excepcion'], 1)
        self.assertEqual(indicadores['correos_pendientes'], 1)
        self.assertEqual(indicadores['valor_solicitado'], Decimal('1000000'))
        self.assertContains(respuesta, self.otra_institucion.nombre_comercial)

    def test_listado_global_filtra_y_pagina_25_registros(self):
        self._autorizar_consulta_completa()
        for indice in range(26):
            self._solicitud(
                f'PAG-OPS-{indice:02d}',
                institucion=(
                    self.institucion if indice < 25 else self.otra_institucion
                ),
                nombre_curso='Programa Operativo',
            )
        self.client.force_login(self.operador)

        primera = self.client.get(
            self.listado,
            {
                'programa': 'Programa Operativo',
                'orden': 'referencia_externa',
            },
        )
        segunda = self.client.get(
            self.listado,
            {
                'programa': 'Programa Operativo',
                'orden': 'referencia_externa',
                'page': 2,
            },
        )

        self.assertEqual(len(primera.context['pagina'].object_list), 25)
        self.assertEqual(len(segunda.context['pagina'].object_list), 1)
        self.assertContains(
            primera,
            'programa=Programa+Operativo&amp;orden=referencia_externa&amp;page=2',
        )
        self.assertContains(
            primera,
            '<details class="edu-dashboard-advanced-filters" open>',
        )

    def test_filtros_invalidos_responden_400_sin_resultados(self):
        self._autorizar_consulta_completa()
        solicitud = self._solicitud('NO-MOSTRAR-INVALIDA')
        self.client.force_login(self.operador)
        for parametros in (
            {'estado': 'ESTADO_PRIVADO'},
            {'institucion': str(uuid4())},
            {'etapa': 'ETAPA_INVENTADA'},
            {'orden': 'correo'},
            {'desde': '2026-10-02', 'hasta': '2026-10-01'},
        ):
            with self.subTest(parametros=parametros):
                respuesta = self.client.get(self.listado, parametros)
                self.assertEqual(respuesta.status_code, 400)
                self.assertNotContains(
                    respuesta,
                    solicitud.referencia_externa,
                    status_code=400,
                )

    def test_bandejas_derivan_estados_reales_y_permiten_solapamiento(self):
        self._autorizar_consulta_completa()
        solicitud = self._solicitud(
            'BANDEJA-MULTIPLE',
            estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self._proceso(
            solicitud,
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            razon=(
                CodigoRazonAutomatizacionEducativa.SIGNATURE_SEND_AMBIGUOUS
            ),
        )
        documento = DocumentoFinanciacion.objects.create(
            solicitud=solicitud,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            referencia_almacenamiento='/privado/no-exponer.pdf',
            nombre_original='persona.pdf',
            content_type='application/pdf',
            estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
            estado_validacion=EstadoValidacionDocumento.PENDING,
        )
        ValidacionIADocumento.objects.create(
            documento=documento,
            numero=1,
            estado=EstadoValidacionIADocumento.MANUAL_REVIEW,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
        )
        self.client.force_login(self.operador)

        respuesta = self.client.get(self.bandejas)
        cantidades = {
            bandeja['codigo']: bandeja['cantidad']
            for bandeja in respuesta.context['bandejas']
        }

        self.assertEqual(cantidades['revision_manual'], 1)
        self.assertEqual(cantidades['error_automatizacion'], 1)
        self.assertEqual(cantidades['documento_inconcluso'], 1)
        self.assertEqual(cantidades['firma_ambigua'], 1)

    def test_detalle_aplica_permiso_integral_y_no_expone_secretos(self):
        self._autorizar(
            self.operador,
            PERMISO_ACCESO,
            PERMISO_SOLICITUDES,
            PERMISO_DOCUMENTOS,
            PERMISO_PROCESOS,
        )
        solicitud = self._solicitud(
            'DETALLE-PRIVADO',
            correo='persona.completa@example.test',
            celular='3105556677',
            numero_documento_estudiante='1006442329',
            direccion='Direccion privada 123',
        )
        documento = DocumentoFinanciacion.objects.create(
            solicitud=solicitud,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            referencia_almacenamiento='/privado/archivo-secreto.pdf',
            nombre_original='nombre-personal.pdf',
            content_type='application/pdf',
            estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
            estado_validacion=EstadoValidacionDocumento.PENDING,
            resultado_procesamiento={'raw_prompt': 'PROMPT_SECRETO'},
        )
        ValidacionIADocumento.objects.create(
            documento=documento,
            numero=1,
            estado=EstadoValidacionIADocumento.MANUAL_REVIEW,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
            proveedor='openai',
            modelo='modelo-controlado',
            confianza=Decimal('0.8000'),
            calidad=Decimal('0.9000'),
            legibilidad=Decimal('0.8500'),
            hallazgos=['LOW_CONFIDENCE'],
            resultado_estructurado={
                'policy_version': 'EDU_IDENTITY_V4',
                'provider_raw': 'RESPUESTA_CRUDA_SECRETA',
            },
        )
        self._proceso(
            solicitud,
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            razon=CodigoRazonAutomatizacionEducativa.PROVIDER_ERROR,
        )
        self._correo(
            solicitud,
            EstadoOutboxCorreoEducativo.AMBIGUOUS,
            'a' + uuid4().hex,
        )
        self.client.force_login(self.operador)

        limitada = self.client.get(self._detalle(solicitud))
        self.assertNotContains(limitada, 'persona.completa@example.test')
        self.assertNotContains(limitada, '1006442329')
        self.assertContains(limitada, '******2329')
        self.assertContains(limitada, 'LOW_CONFIDENCE')
        for secreto in (
            '/privado/archivo-secreto.pdf',
            'nombre-personal.pdf',
            'PROMPT_SECRETO',
            'RESPUESTA_CRUDA_SECRETA',
            'persona-privada@example.test',
            'CONTEXTO_SECRETO',
        ):
            self.assertNotContains(limitada, secreto)

        self._autorizar(self.operador, PERMISO_DATOS_INTEGRALES)
        integral = self.client.get(self._detalle(solicitud))
        self.assertContains(integral, 'persona.completa@example.test')
        self.assertContains(integral, '1006442329')

    def test_sin_permisos_documentales_o_procesos_muestra_acceso_restringido(self):
        self._autorizar(
            self.operador,
            PERMISO_ACCESO,
            PERMISO_SOLICITUDES,
        )
        solicitud = self._solicitud('CAPACIDAD-LIMITADA')
        self.client.force_login(self.operador)

        respuesta = self.client.get(self._detalle(solicitud))

        self.assertContains(respuesta, 'Requiere permiso de consulta documental')
        self.assertContains(respuesta, 'Requiere permiso de consulta de procesos')

    def test_detalle_inexistente_responde_404(self):
        self._autorizar_consulta_completa()
        self.client.force_login(self.operador)

        respuesta = self.client.get(
            reverse(
                'financiacion_educativa_web:operaciones:solicitud-detalle',
                kwargs={'application_id': uuid4()},
            )
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_paginas_son_no_store_get_only_y_sidebar_independiente(self):
        self._autorizar_consulta_completa()
        solicitud = self._solicitud('HTTP-SEGURO')
        self.client.force_login(self.operador)
        for url in (
            self.inicio,
            self.listado,
            self.bandejas,
            self.instituciones,
            self._detalle(solicitud),
        ):
            respuesta = self.client.get(url)
            self.assertIn('no-store', respuesta.headers['Cache-Control'])
            self.assertContains(
                respuesta,
                'aria-label="Navegaci&oacute;n operativa"',
            )
            self.assertEqual(self.client.post(url).status_code, 405)
        self.assertNotContains(respuesta, 'Cambiar instituci&oacute;n')

    def test_consultas_estan_acotadas(self):
        self._autorizar_consulta_completa()
        for indice in range(5):
            self._solicitud(f'QUERY-OPS-{indice}')
        self.client.force_login(self.operador)
        with CaptureQueriesContext(connection) as consultas_listado:
            listado = self.client.get(self.listado)
        solicitud = self._solicitud('QUERY-OPS-DETALLE')
        with CaptureQueriesContext(connection) as consultas_detalle:
            detalle = self.client.get(self._detalle(solicitud))

        self.assertEqual(listado.status_code, 200)
        self.assertEqual(detalle.status_code, 200)
        self.assertLessEqual(len(consultas_listado), 15)
        self.assertLessEqual(len(consultas_detalle), 22)
