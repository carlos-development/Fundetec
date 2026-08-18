from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoEscaneoDocumento,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    OrigenCapturaDocumento,
    TipoArtefactoContractualEducativo,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.dashboards.institucional.permissions import (
    SESSION_MEMBRESIA_INSTITUCIONAL_ID,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    DocumentoFinanciacion,
    HistorialEstadoSolicitud,
    ProcesoFirmaEducativa,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)
from instituciones.models import Institucion, MembresiaInstitucion
from instituciones.services.membresias import crear_membresia, desactivar_membresia


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
)
class DashboardInstitucionalConsultaTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_user(
            username='actor-consulta@example.test',
            email='actor-consulta@example.test',
            password='Clave-2026',
        )
        self.usuario = User.objects.create_user(
            username='consulta@example.test',
            email='consulta@example.test',
            password='Clave-2026',
        )
        self.institucion = self._institucion('Principal', '901700001')
        self.otra_institucion = self._institucion('Ajena', '901700002')
        self.membresia = self._membresia(
            self.usuario,
            self.institucion,
            MembresiaInstitucion.Rol.INSTITUTION_ANALYST,
        )
        self.client.force_login(self.usuario)
        self.inicio = reverse('financiacion_educativa_web:institucion:inicio')
        self.listado = reverse(
            'financiacion_educativa_web:institucion:solicitudes'
        )
        self.seguimiento = reverse(
            'financiacion_educativa_web:institucion:seguimiento'
        )

    @staticmethod
    def _institucion(nombre, nit):
        return Institucion.objects.create(
            nombre_comercial=f'Instituto {nombre}',
            razon_social=f'Instituto {nombre} SAS',
            numero_identificacion_tributaria=nit,
        )

    def _membresia(self, usuario, institucion, rol):
        return crear_membresia(
            usuario=usuario,
            institucion=institucion,
            rol=rol,
            actor=self.actor,
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
            'financiacion_educativa_web:institucion:solicitud-detalle',
            kwargs={'application_id': solicitud.pk},
        )

    def _seleccionar(self, membresia):
        sesion = self.client.session
        sesion[SESSION_MEMBRESIA_INSTITUCIONAL_ID] = str(membresia.pk)
        sesion.save()

    def test_rutas_funcionales_son_estables(self):
        self.assertEqual(
            self.listado,
            '/financiacion-educativa/institucion/solicitudes/',
        )
        self.assertEqual(
            self.seguimiento,
            '/financiacion-educativa/institucion/seguimiento/',
        )

    def test_indicadores_y_recientes_se_aislan_por_institucion(self):
        estados = (
            EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
            EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
            EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
            EstadoSolicitudFinanciacion.APPROVED,
            EstadoSolicitudFinanciacion.REJECTED,
        )
        for indice, estado in enumerate(estados):
            self._solicitud(
                f'PROPIA-{indice}',
                estado=estado,
                valor_plan=Decimal('100000.00'),
            )
        ajena = self._solicitud(
            'AJENA-SECRETA',
            institucion=self.otra_institucion,
            estado=EstadoSolicitudFinanciacion.APPROVED,
            valor_plan=Decimal('9000000.00'),
        )

        respuesta = self.client.get(self.inicio)
        indicadores = respuesta.context['indicadores']

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(indicadores['total'], 7)
        self.assertEqual(indicadores['recibidas'], 1)
        self.assertEqual(indicadores['en_proceso'], 1)
        self.assertEqual(indicadores['correccion'], 1)
        self.assertEqual(indicadores['revision_manual'], 1)
        self.assertEqual(indicadores['firma'], 1)
        self.assertEqual(indicadores['aprobadas'], 1)
        self.assertEqual(indicadores['rechazadas_cerradas'], 1)
        self.assertEqual(indicadores['valor_solicitado'], Decimal('700000'))
        self.assertNotContains(respuesta, ajena.referencia_externa)

    def test_valor_financiado_suma_solo_fotografias_activas_del_tenant(self):
        crear_configuracion_financiera()
        propia = self._solicitud('FOTO-PROPIA')
        propia.plazo_meses = 3
        propia.save(update_fields=['plazo_meses'])
        ajena = self._solicitud('FOTO-AJENA', institucion=self.otra_institucion)
        ajena.plazo_meses = 3
        ajena.save(update_fields=['plazo_meses'])
        foto_propia = crear_fotografia_condiciones_financieras(
            propia,
            fecha_inicio_plan=date(2026, 9, 1),
            actor=self.actor,
        )
        crear_fotografia_condiciones_financieras(
            ajena,
            fecha_inicio_plan=date(2026, 9, 1),
            actor=self.actor,
        )

        respuesta = self.client.get(self.inicio)

        self.assertEqual(
            respuesta.context['indicadores']['valor_financiado'],
            foto_propia.capital_financiado,
        )

    def test_detalle_muestra_plan_y_fotografia_sin_anticipar_autorizacion(self):
        crear_configuracion_financiera()
        solicitud = self._solicitud('FINANZAS-DETALLE')
        solicitud.plazo_meses = 3
        solicitud.save(update_fields=['plazo_meses'])
        fotografia = crear_fotografia_condiciones_financieras(
            solicitud,
            fecha_inicio_plan=date(2026, 9, 1),
            actor=self.actor,
            bloquear=True,
        )

        respuesta = self.client.get(self._detalle(solicitud))

        self.assertContains(respuesta, '$1.000.000')
        self.assertContains(
            respuesta,
            f'${fotografia.capital_financiado:,.0f}'.replace(',', '.'),
        )
        self.assertContains(respuesta, 'No autorizada')

    def test_detalle_resume_firma_sin_exponer_identificadores_privados(self):
        crear_configuracion_financiera()
        solicitud = self._solicitud('FIRMA-DETALLE')
        solicitud.plazo_meses = 3
        solicitud.save(update_fields=['plazo_meses'])
        fotografia = crear_fotografia_condiciones_financieras(
            solicitud,
            fecha_inicio_plan=date(2026, 9, 1),
            actor=self.actor,
            bloquear=True,
        )
        pagare = ArtefactoContractualEducativo.objects.create(
            solicitud=solicitud,
            fotografia_financiera=fotografia,
            tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
            numero_version=1,
            estado=EstadoArtefactoContractualEducativo.SENT_FOR_SIGNATURE,
            numero_documento='PAGARE-PRIVADO-001',
            version_plantilla='TEST-V1',
            archivo='contractuales/ruta-privada-pagare.pdf',
            hash_sha256='a' * 64,
            tamano_bytes=128,
        )
        ProcesoFirmaEducativa.objects.create(
            solicitud=solicitud,
            artefacto=pagare,
            estado=EstadoProcesoFirmaEducativa.SENT,
            external_id='EXTERNAL-ID-PRIVADO',
            token_documento_externo='TOKEN-DOCUMENTO-PRIVADO',
            destinatario_hmac='b' * 64,
            intentos_envio=1,
            enviado_en=timezone.now(),
        )

        respuesta = self.client.get(self._detalle(solicitud))

        self.assertContains(respuesta, 'Enviado a firma')
        self.assertContains(respuesta, 'Pendiente de firma')
        for secreto in (
            'PAGARE-PRIVADO-001',
            'contractuales/ruta-privada-pagare.pdf',
            'EXTERNAL-ID-PRIVADO',
            'TOKEN-DOCUMENTO-PRIVADO',
            'b' * 64,
        ):
            self.assertNotContains(respuesta, secreto)

    def test_inicio_vacio_es_claro(self):
        respuesta = self.client.get(self.inicio)
        self.assertContains(respuesta, 'A&uacute;n no hay solicitudes', html=True)
        self.assertEqual(respuesta.context['indicadores']['total'], 0)

    def test_listado_y_seguimiento_solo_muestran_solicitudes_propias(self):
        propia = self._solicitud('VISIBLE-PROPIA')
        aprobada = self._solicitud(
            'CERRADA-PROPIA',
            estado=EstadoSolicitudFinanciacion.APPROVED,
        )
        ajena = self._solicitud(
            'OCULTA-AJENA',
            institucion=self.otra_institucion,
        )

        listado = self.client.get(self.listado)
        seguimiento = self.client.get(self.seguimiento)

        self.assertContains(listado, propia.referencia_externa)
        self.assertContains(listado, aprobada.referencia_externa)
        self.assertNotContains(listado, ajena.referencia_externa)
        self.assertContains(seguimiento, propia.referencia_externa)
        self.assertNotContains(seguimiento, aprobada.referencia_externa)
        self.assertNotContains(seguimiento, ajena.referencia_externa)

    def test_filtros_principales_visibles_y_avanzados_cerrados_por_defecto(self):
        respuesta = self.client.get(self.listado)
        contenido = respuesta.content.decode()

        self.assertContains(respuesta, 'name="q"')
        self.assertContains(respuesta, 'name="estado"')
        self.assertContains(respuesta, 'Consultar')
        self.assertContains(respuesta, 'Limpiar')
        self.assertContains(respuesta, 'Filtros avanzados')
        self.assertIn(
            '<details class="edu-dashboard-advanced-filters">',
            contenido,
        )
        self.assertNotIn(
            '<details class="edu-dashboard-advanced-filters" open>',
            contenido,
        )

    def test_filtros_avanzados_activos_abren_bloque_y_muestran_contador(self):
        self._solicitud(
            'FILTROS-AVANZADOS',
            nombre_curso='Ingles B2',
        )

        respuesta = self.client.get(
            self.listado,
            {
                'programa': 'Ingles B2',
                'desde': '2026-01-01',
                'orden': 'valor_plan',
            },
        )
        contenido = respuesta.content.decode()

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(
            '<details class="edu-dashboard-advanced-filters" open>',
            contenido,
        )
        self.assertContains(
            respuesta,
            'aria-label="3 filtros avanzados activos"',
        )
        self.assertEqual(
            respuesta.context['formulario_filtros'].cleaned_data['programa'],
            'Ingles B2',
        )
        self.assertEqual(
            respuesta.context['formulario_filtros'].cleaned_data['orden'],
            'valor_plan',
        )

    def test_solicitudes_y_seguimiento_reutilizan_partial_de_filtros(self):
        partial = (
            'financiacion_educativa/dashboards/institucional/'
            '_filtros_solicitudes.html'
        )

        for url in (self.listado, self.seguimiento):
            with self.subTest(url=url):
                respuesta = self.client.get(url)
                plantillas = [
                    plantilla.name
                    for plantilla in respuesta.templates
                    if plantilla.name
                ]
                self.assertEqual(plantillas.count(partial), 1)
                self.assertContains(
                    respuesta,
                    f'href="{url}">Limpiar</a>',
                )

    def test_busqueda_y_filtros_controlados(self):
        objetivo = self._solicitud(
            'BUSCAR-OBJETIVO',
            estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
            nombres='LAURA',
            apellidos='CONTROLADA',
            correo='laura.controlada@example.test',
            nombre_curso='Ingles B2',
            periodo_academico='2026-2',
            sede='Centro',
        )
        self._solicitud(
            'NO-COINCIDE',
            nombre_curso='Sistemas',
            periodo_academico='2027-1',
            sede='Norte',
        )
        parametros = {
            'q': 'laura.controlada@example.test',
            'estado': 'UNDER_REVIEW',
            'programa': 'Ingles B2',
            'periodo': '2026-2',
            'sede': 'Centro',
            'orden': 'referencia_externa',
        }

        respuesta = self.client.get(self.listado, parametros)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, objetivo.referencia_externa)
        self.assertNotContains(respuesta, 'NO-COINCIDE')
        formulario = respuesta.context['formulario_filtros']
        self.assertEqual(formulario.cleaned_data['q'], parametros['q'])
        self.assertEqual(formulario.cleaned_data['estado'], parametros['estado'])
        self.assertEqual(
            formulario.cleaned_data['programa'],
            parametros['programa'],
        )
        self.assertEqual(formulario.cleaned_data['periodo'], parametros['periodo'])
        self.assertEqual(formulario.cleaned_data['sede'], parametros['sede'])

    def test_parametros_invalidos_responden_400_sin_datos(self):
        solicitud = self._solicitud('NO-FILTRAR-INVALIDO')
        for parametros in (
            {'estado': 'PRIVATE_INTERNAL_STATE'},
            {'orden': 'correo'},
            {'programa': 'Programa ajeno'},
            {'desde': '2026-10-02', 'hasta': '2026-10-01'},
            {'page': 'invalida'},
        ):
            with self.subTest(parametros=parametros):
                respuesta = self.client.get(self.listado, parametros)
                self.assertEqual(respuesta.status_code, 400)
                self.assertNotContains(
                    respuesta,
                    solicitud.referencia_externa,
                    status_code=400,
                )

    def test_rango_de_fechas_y_ordenamiento_cerrado(self):
        antigua = self._solicitud('FECHA-B')
        reciente = self._solicitud('FECHA-A')
        type(antigua).objects.filter(pk=antigua.pk).update(
            creada_en=timezone.make_aware(datetime(2026, 1, 10, 8, 0))
        )
        type(reciente).objects.filter(pk=reciente.pk).update(
            creada_en=timezone.make_aware(datetime(2026, 2, 10, 8, 0))
        )

        respuesta = self.client.get(
            self.listado,
            {
                'desde': '2026-02-01',
                'hasta': '2026-02-28',
                'orden': 'referencia_externa',
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, reciente.referencia_externa)
        self.assertNotContains(respuesta, antigua.referencia_externa)

    def test_paginacion_de_25_y_conservacion_de_filtros(self):
        for indice in range(26):
            self._solicitud(
                f'PAG-{indice:02d}',
                nombre_curso='Programa Paginado',
            )

        primera = self.client.get(
            self.listado,
            {'programa': 'Programa Paginado'},
        )
        segunda = self.client.get(
            self.listado,
            {'programa': 'Programa Paginado', 'page': 2},
        )

        self.assertEqual(len(primera.context['pagina'].object_list), 25)
        self.assertEqual(len(segunda.context['pagina'].object_list), 1)
        self.assertContains(
            primera,
            'programa=Programa+Paginado&amp;page=2',
        )

    def test_detalle_ajeno_e_inexistente_devuelven_mismo_404(self):
        ajena = self._solicitud(
            'DETALLE-AJENO',
            institucion=self.otra_institucion,
        )

        for identificador in (ajena.pk, uuid4()):
            respuesta = self.client.get(
                reverse(
                    'financiacion_educativa_web:institucion:solicitud-detalle',
                    kwargs={'application_id': identificador},
                )
            )
            self.assertEqual(respuesta.status_code, 404)
            self.assertNotContains(
                respuesta,
                ajena.referencia_externa,
                status_code=404,
            )

    def test_enmascaramiento_por_rol_y_documento_siempre_parcial(self):
        solicitud = self._solicitud(
            'PII-CONTROLADA',
            correo='persona.completa@example.test',
            celular='3105556677',
            tipo_documento_estudiante=TipoDocumentoIdentidad.CC,
            numero_documento_estudiante='1006442329',
            fecha_nacimiento_estudiante=date(2000, 1, 1),
        )

        for rol in MembresiaInstitucion.Rol.values:
            self.membresia.rol = rol
            self.membresia.save(update_fields=['rol'])
            respuesta = self.client.get(self._detalle(solicitud))
            self.assertNotContains(respuesta, '1006442329')
            self.assertContains(respuesta, '******2329')
            if rol == MembresiaInstitucion.Rol.INSTITUTION_READ_ONLY:
                self.assertNotContains(respuesta, 'persona.completa@example.test')
                self.assertNotContains(respuesta, '3105556677')
                self.assertContains(respuesta, 'p***@example.test')
                self.assertContains(respuesta, '******6677')
            else:
                self.assertContains(respuesta, 'persona.completa@example.test')
                self.assertContains(respuesta, '3105556677')

    def test_detalle_documental_no_filtra_metadatos_internos(self):
        solicitud = self._solicitud('DOC-SEGURO')
        DocumentoFinanciacion.objects.create(
            solicitud=solicitud,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            referencia_almacenamiento='/private/secret-document.pdf',
            nombre_original='persona-real.pdf',
            content_type='application/pdf',
            estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
            estado_validacion=EstadoValidacionDocumento.REJECTED,
            observacion_revision='OBSERVACION_INTERNA_SECRETA',
            resultado_procesamiento={'provider_response': 'RAW_AI_SECRET'},
        )

        respuesta = self.client.get(self._detalle(solicitud))

        self.assertContains(respuesta, 'Certificado de ingresos')
        self.assertContains(respuesta, 'Requiere correcci&oacute;n', html=True)
        for secreto in (
            '/private/secret-document.pdf',
            'persona-real.pdf',
            'OBSERVACION_INTERNA_SECRETA',
            'RAW_AI_SECRET',
        ):
            self.assertNotContains(respuesta, secreto)

    def test_linea_tiempo_transforma_y_no_expone_motivo_o_metadata(self):
        solicitud = self._solicitud('HISTORIAL-PUBLICO')
        HistorialEstadoSolicitud.objects.create(
            solicitud=solicitud,
            estado_anterior=None,
            estado_nuevo=EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
            motivo='MOTIVO_INTERNO_SECRETO',
            metadata={'lease_id': 'LEASE_INTERNO'},
        )
        HistorialEstadoSolicitud.objects.create(
            solicitud=solicitud,
            estado_anterior=EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
            estado_nuevo=EstadoSolicitudFinanciacion.PENDING_TERMS,
            motivo='STACK_TRACE_INTERNO',
        )

        respuesta = self.client.get(self._detalle(solicitud))

        self.assertContains(respuesta, 'Recibida')
        self.assertContains(respuesta, 'Requiere accion del solicitante')
        for interno in (
            'MOTIVO_INTERNO_SECRETO',
            'LEASE_INTERNO',
            'STACK_TRACE_INTERNO',
            'PENDING_USER_REGISTRATION',
        ):
            self.assertNotContains(respuesta, interno)

    def test_cambio_de_institucion_cambia_datos_visibles(self):
        propia = self._solicitud('TENANT-UNO')
        otra = self._solicitud(
            'TENANT-DOS',
            institucion=self.otra_institucion,
        )
        segunda = self._membresia(
            self.usuario,
            self.otra_institucion,
            MembresiaInstitucion.Rol.INSTITUTION_ANALYST,
        )
        self._seleccionar(self.membresia)
        primera_respuesta = self.client.get(self.listado)
        self._seleccionar(segunda)
        segunda_respuesta = self.client.get(self.listado)

        self.assertContains(primera_respuesta, propia.referencia_externa)
        self.assertNotContains(primera_respuesta, otra.referencia_externa)
        self.assertContains(segunda_respuesta, otra.referencia_externa)
        self.assertNotContains(segunda_respuesta, propia.referencia_externa)

    def test_paginas_sensibles_no_se_cachean_y_sidebar_esta_habilitado(self):
        solicitud = self._solicitud('CACHE-CONTROL')
        for url in (
            self.inicio,
            self.listado,
            self.seguimiento,
            self._detalle(solicitud),
        ):
            respuesta = self.client.get(url)
            self.assertIn('no-store', respuesta.headers['Cache-Control'])
        self.assertContains(respuesta, 'Solicitudes')
        self.assertContains(respuesta, 'Seguimiento')

    def test_revocacion_impide_listado_y_detalle_y_no_hay_mutaciones_http(self):
        solicitud = self._solicitud('REVOCADA-CONSULTA')
        detalle = self._detalle(solicitud)
        self.assertEqual(self.client.post(detalle).status_code, 405)
        desactivar_membresia(membresia=self.membresia, actor=self.actor)

        self.assertEqual(self.client.get(self.listado).status_code, 403)
        self.assertEqual(self.client.get(detalle).status_code, 403)

    def test_consultas_controladas_sin_n_mas_uno(self):
        for indice in range(5):
            self._solicitud(f'QUERY-{indice}')
        with CaptureQueriesContext(connection) as consultas_listado:
            respuesta_listado = self.client.get(self.listado)
        solicitud = self._solicitud('QUERY-DETALLE')
        with CaptureQueriesContext(connection) as consultas_detalle:
            respuesta_detalle = self.client.get(self._detalle(solicitud))

        self.assertEqual(respuesta_listado.status_code, 200)
        self.assertEqual(respuesta_detalle.status_code, 200)
        self.assertLessEqual(len(consultas_listado), 12)
        self.assertLessEqual(len(consultas_detalle), 12)
