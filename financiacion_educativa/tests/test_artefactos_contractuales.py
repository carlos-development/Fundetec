import hashlib
import re
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connections, transaction
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    RelacionEstudiante,
    RolParticipante,
    TipoArtefactoContractualEducativo,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import ArtefactoContractualEducativo
from financiacion_educativa.services.artefactos_contractuales import (
    generar_artefactos_contractuales,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)
from financiacion_educativa.web.views import (
    descargar_artefacto_contractual_view,
)


@override_settings(
    FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL=(
        'APROBADO SOLUCIONES DIGITALES S.A.S.'
    ),
    FINANCIACION_EDUCATIVA_ACREEDOR_NIT='900000000-1',
    FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL='REPRESENTANTE PRUEBA',
    FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO='Bogota D.C.',
    FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA='1',
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION=(
        'La obligacion se rige por los terminos completos contenidos en este '
        'pagare educativo.'
    ),
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES=(
        'Estas instrucciones se aplican exclusivamente a la obligacion '
        'educativa identificada en este paquete.'
    ),
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO=(
        'El incumplimiento produce unicamente los efectos legales y '
        'contractuales expresamente establecidos en este documento.'
    ),
)
class ArtefactosContractualesTests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.addCleanup(self.private_root.cleanup)
        self.override_private = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name,
        )
        self.override_private.enable()
        self.addCleanup(self.override_private.disable)
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='contratos@example.com',
            email='contratos@example.com',
            password='Clave-2026',
        )
        self.otro_usuario = User.objects.create_user(
            username='otro-contratos@example.com',
            email='otro-contratos@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.plazo_meses = 3
        self.solicitud.save(update_fields=['estado', 'plazo_meses'])
        crear_configuracion_financiera()

    def _participante(self, *, menor=False, tutor=False):
        if tutor:
            return registrar_o_actualizar_participante(
                solicitud=self.solicitud,
                actor=self.usuario,
                datos=DatosParticipante(
                    nombres='TUTOR',
                    apellidos='RESPONSABLE',
                    tipo_documento=TipoDocumentoIdentidad.CC,
                    numero_documento='900100200',
                    fecha_nacimiento=date(1980, 1, 1),
                    correo='tutor@example.com',
                    telefono='3010000000',
                    relacion_estudiante=RelacionEstudiante.LEGAL_GUARDIAN,
                ),
                roles={
                    RolParticipante.GUARDIAN,
                    RolParticipante.PRINCIPAL_DEBTOR,
                },
            )
        roles = {RolParticipante.STUDENT}
        if not menor:
            roles.add(RolParticipante.PRINCIPAL_DEBTOR)
        return registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='ESTUDIANTE',
                apellidos='EDUCATIVO',
                tipo_documento=(
                    TipoDocumentoIdentidad.TI
                    if menor
                    else TipoDocumentoIdentidad.CC
                ),
                numero_documento='100200300',
                fecha_nacimiento=(
                    date(2012, 1, 1) if menor else date(1990, 1, 1)
                ),
                correo='estudiante@example.com',
                telefono='3000000000',
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles=roles,
        )

    def _preparar_finanzas(self):
        fotografia = crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 8, 4),
            actor=self.usuario,
            bloquear=True,
        )
        self.solicitud.estado = (
            EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
        )
        self.solicitud.save(update_fields=['estado'])
        return fotografia

    def _texto_pdf(self, artefacto):
        with artefacto.archivo.open('rb') as archivo:
            texto = '\n'.join(
                pagina.extract_text() or ''
                for pagina in PdfReader(archivo).pages
            )
        return re.sub(r'\s+', ' ', texto)

    def test_genera_paquete_y_ficha_privados_versionados_e_idempotentes(self):
        self._participante()
        fotografia = self._preparar_finanzas()

        primera = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        segunda = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )

        self.assertEqual(ArtefactoContractualEducativo.objects.count(), 2)
        self.assertEqual(primera.pagare.pk, segunda.pagare.pk)
        self.assertEqual(primera.ficha_matricula.pk, segunda.ficha_matricula.pk)
        for artefacto in (primera.pagare, primera.ficha_matricula):
            self.assertEqual(artefacto.fotografia_financiera, fotografia)
            self.assertEqual(artefacto.numero_version, 1)
            with artefacto.archivo.open('rb') as archivo:
                contenido = archivo.read()
            self.assertTrue(contenido.startswith(b'%PDF'))
            self.assertEqual(
                artefacto.hash_sha256,
                hashlib.sha256(contenido).hexdigest(),
            )
            with self.assertRaises(ValueError):
                artefacto.archivo.url

        texto_pagare = self._texto_pdf(primera.pagare)
        texto_ficha = self._texto_pdf(primera.ficha_matricula)
        self.assertIn('ESTUDIANTE EDUCATIVO', texto_pagare)
        self.assertIn('1142711', texto_pagare.replace('.', '').replace(',', ''))
        self.assertNotIn('libranza', texto_pagare.lower())
        self.assertNotIn('nomina', texto_pagare.lower())
        self.assertNotIn('he recibido un desembolso', texto_pagare.lower())
        self.assertIn('no implica entrega ni desembolso', texto_pagare.lower())
        self.assertIn('CARTA DE INSTRUCCIONES', texto_pagare)
        self.assertIn('HABEAS DATA', texto_pagare.upper())
        self.assertIn('FICHA DE MATR', texto_pagare)
        self.assertNotIn('financiera No.', texto_pagare)
        self.assertNotIn('PRUEBAS', texto_pagare.upper())
        self.assertEqual(
            primera.pagare.version_plantilla,
            'PAQUETE-EDU-3.0-1',
        )
        self.assertEqual(
            primera.ficha_matricula.version_plantilla,
            'FO-AD-005-V2-EDU-2',
        )
        self.assertIn('ficha de matr', texto_ficha.lower())
        self.assertIn('informaci', texto_ficha.lower())
        self.assertIn('matr', texto_ficha.lower())
        self.assertIn('retiro', texto_ficha.lower())
        self.assertIn('No aplica', texto_ficha)
        self.assertNotIn('No aplica / no informado', texto_ficha)
        with primera.ficha_matricula.archivo.open('rb') as archivo:
            lector = PdfReader(archivo)
            self.assertEqual(len(lector.pages), 1)
            self.assertGreaterEqual(len(lector.pages[0].images), 1)

        with primera.pagare.archivo.open('rb') as archivo:
            lector = PdfReader(archivo)
            self.assertEqual(len(lector.pages), 4)
            paginas = [re.sub(r'\s+', ' ', p.extract_text() or '') for p in lector.pages]
        self.assertIn('PAGAR', paginas[0])
        self.assertIn('CARTA DE INSTRUCCIONES', paginas[1])
        self.assertIn('HABEAS DATA', paginas[2].upper())
        self.assertIn('FICHA DE MATR', paginas[3])

    def test_paquete_presentado_no_expone_marcadores_tecnicos_o_de_desarrollo(self):
        self._participante()
        self._preparar_finanzas()

        resultado = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        texto = self._texto_pdf(resultado.pagare)
        plantilla = (
            Path(settings.BASE_DIR)
            / 'templates'
            / 'financiacion_educativa'
            / 'documentos'
            / 'paquete_contractual_v3.html'
        ).read_text(encoding='utf-8')

        self.assertNotIn('Fotograf&iacute;a financiera No.', plantilla)
        self.assertNotIn('Fotografia financiera No.', plantilla)
        self.assertNotIn('PRUEBAS', plantilla.upper())
        self.assertNotIn('PLACEHOLDER', plantilla.upper())
        self.assertNotIn('financiera No.', texto)
        self.assertNotIn('PRUEBAS', texto.upper())
        self.assertNotIn('PLACEHOLDER', texto.upper())

    def test_paquete_usa_desglose_de_fotografia_bloqueada_sin_recalcular(self):
        self._participante()
        fotografia = self._preparar_finanzas()

        resultado = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        texto = self._texto_pdf(resultado.pagare)

        self.assertTrue(fotografia.bloqueada)
        self.assertIn('1.000.000', texto)
        self.assertIn('100.000', texto)
        self.assertIn('19.000', texto)
        self.assertIn('20.000', texto)
        self.assertIn('3.711', texto)
        self.assertIn('1.142.711', texto)
        self.assertIn(fotografia.proveedor_fondo_garantias, texto)
        self.assertIn(fotografia.proveedor_seguro_vida, texto)

    def test_ficha_usa_datos_reales_y_no_inventa_campos_ausentes(self):
        self.solicitud.codigo_matricula = 'MAT-SINTETICA-2026'
        self.solicitud.periodo_academico = '2026-2'
        self.solicitud.sede = 'Sede Centro'
        self.solicitud.jornada = 'Nocturna'
        self.solicitud.save(
            update_fields=[
                'codigo_matricula',
                'periodo_academico',
                'sede',
                'jornada',
            ]
        )
        self._participante()
        self._preparar_finanzas()

        resultado = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        texto = self._texto_pdf(resultado.pagare)

        self.assertIn('MAT-SINTETICA-2026', texto)
        self.assertIn('2026-2', texto)
        self.assertIn('Sede:Sede Centro', texto)
        self.assertIn('Jornada:Nocturna', texto)
        self.assertIn('TECNICO EN SISTEMAS', texto.upper())
        self.assertIn('No informado', texto)
        self.assertNotIn('Firma Rector', texto)
        self.assertNotIn('Firma de la Secretario', texto)

    def test_artefactos_historicos_no_se_regeneran_ni_reversionan(self):
        self._participante()
        fotografia = self._preparar_finanzas()
        historicos = {}
        for tipo, prefijo, version in (
            (
                TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
                'PE-HISTORICO',
                'PAGARE-2.0-EDU-1',
            ),
            (
                TipoArtefactoContractualEducativo.ENROLLMENT_FORM,
                'FM-HISTORICA',
                'FO-AD-005-V2-EDU-1',
            ),
        ):
            contenido = f'%PDF-1.4\n% {prefijo}\n%%EOF'.encode()
            artefacto = ArtefactoContractualEducativo(
                solicitud=self.solicitud,
                fotografia_financiera=fotografia,
                tipo=tipo,
                numero_version=1,
                numero_documento=prefijo,
                version_plantilla=version,
                hash_sha256=hashlib.sha256(contenido).hexdigest(),
                tamano_bytes=len(contenido),
                generado_por=self.usuario,
            )
            artefacto.archivo.save(
                f'{prefijo}.pdf',
                ContentFile(contenido),
                save=False,
            )
            artefacto.full_clean()
            artefacto.save()
            historicos[tipo] = (artefacto, contenido)

        resultado = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )

        self.assertEqual(resultado.pagare.pk, historicos[TipoArtefactoContractualEducativo.PROMISSORY_NOTE][0].pk)
        self.assertEqual(resultado.ficha_matricula.pk, historicos[TipoArtefactoContractualEducativo.ENROLLMENT_FORM][0].pk)
        for artefacto, contenido in historicos.values():
            artefacto.refresh_from_db()
            with artefacto.archivo.open('rb') as archivo:
                self.assertEqual(archivo.read(), contenido)
        self.assertEqual(resultado.pagare.version_plantilla, 'PAGARE-2.0-EDU-1')
        self.assertEqual(resultado.ficha_matricula.version_plantilla, 'FO-AD-005-V2-EDU-1')

    def test_generacion_no_invoca_centrales_ni_servicios_http(self):
        self._participante()
        self._preparar_finanzas()

        with patch('requests.sessions.Session.request') as request_http:
            resultado = generar_artefactos_contractuales(
                solicitud=self.solicitud,
                actor=self.usuario,
            )

        request_http.assert_not_called()
        texto = self._texto_pdf(resultado.pagare)
        self.assertIn('DataCr', texto)

    def test_assets_contractuales_no_incluyen_pdfs_de_referencia(self):
        assets = (
            Path(settings.BASE_DIR)
            / 'financiacion_educativa'
            / 'assets'
            / 'contractual'
        )

        self.assertTrue((assets / 'membrete_aprobado_v3.png').is_file())
        self.assertEqual(list(assets.rglob('*.pdf')), [])

    def test_rechaza_renderizar_weasyprint_dentro_de_transaccion_aplicativa(self):
        self._participante()
        self._preparar_finanzas()

        with transaction.atomic(), self.assertRaisesMessage(
            RuntimeError,
            'El PDF contractual debe renderizarse fuera de una transaccion.',
        ):
            generar_artefactos_contractuales(
                solicitud=self.solicitud,
                actor=self.usuario,
            )

    def test_menor_usa_tutor_como_responsable_y_conserva_estudiante(self):
        self._participante(menor=True)
        self._participante(tutor=True)
        self._preparar_finanzas()

        resultado = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        texto = self._texto_pdf(resultado.pagare)

        self.assertIn('TUTOR RESPONSABLE', texto)
        self.assertIn('ESTUDIANTE EDUCATIVO', texto)

    def test_rechaza_generacion_fuera_del_estado_contractual(self):
        self._participante()
        self._preparar_finanzas()
        self.solicitud.estado = EstadoSolicitudFinanciacion.APPROVED
        self.solicitud.save(update_fields=['estado'])

        with self.assertRaises(ValidationError):
            generar_artefactos_contractuales(
                solicitud=self.solicitud,
                actor=self.usuario,
            )
        self.assertFalse(ArtefactoContractualEducativo.objects.exists())

    @override_settings(
        FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION='',
    )
    def test_rechaza_generacion_sin_clausulas_juridicas_aprobadas(self):
        self._participante()
        self._preparar_finanzas()

        with self.assertRaisesMessage(
            ValidationError,
            'Configura la version y las clausulas juridicas educativas aprobadas.',
        ):
            generar_artefactos_contractuales(
                solicitud=self.solicitud,
                actor=self.usuario,
            )

        self.assertFalse(ArtefactoContractualEducativo.objects.exists())

    def test_descarga_exige_propiedad_de_la_solicitud(self):
        self._participante()
        self._preparar_finanzas()
        artefacto = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        ).pagare
        url = reverse(
            'financiacion_educativa_web:artefacto-descargar',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'artefacto_id': artefacto.pk,
            },
        )

        request_ajeno = RequestFactory().get(url)
        request_ajeno.user = self.otro_usuario
        with self.assertRaises(Http404):
            descargar_artefacto_contractual_view(
                request_ajeno,
                solicitud_id=self.solicitud.pk,
                artefacto_id=artefacto.pk,
            )

        conexion_principal = connections['default']
        self.assertIsNotNone(conexion_principal.connection)
        conexion_db_principal = conexion_principal.connection
        request = RequestFactory().get(url)
        request.user = self.usuario
        respuesta = descargar_artefacto_contractual_view(
            request,
            solicitud_id=self.solicitud.pk,
            artefacto_id=artefacto.pk,
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        contenido = b''.join(respuesta.streaming_content)
        self.assertTrue(contenido.startswith(b'%PDF'))
        archivo_respuesta = respuesta.file_to_stream
        self.assertIsNotNone(archivo_respuesta)
        self.assertFalse(archivo_respuesta.closed)
        archivo_respuesta.close()
        self.assertTrue(archivo_respuesta.closed)

        self.assertIs(conexion_principal.connection, conexion_db_principal)
        self.assertTrue(conexion_principal.is_usable())
        self.assertTrue(
            ArtefactoContractualEducativo.objects.filter(
                pk=artefacto.pk,
            ).exists()
        )

    def test_tipo_de_artefacto_es_separable(self):
        self._participante()
        self._preparar_finanzas()
        generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self.assertSetEqual(
            set(
                ArtefactoContractualEducativo.objects.values_list(
                    'tipo',
                    flat=True,
                )
            ),
            {
                TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
                TipoArtefactoContractualEducativo.ENROLLMENT_FORM,
            },
        )
