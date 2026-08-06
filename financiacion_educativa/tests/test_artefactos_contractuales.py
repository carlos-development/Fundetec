import hashlib
import re
from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
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


@override_settings(
    FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL=(
        'APROBADO SOLUCIONES DIGITALES S.A.S.'
    ),
    FINANCIACION_EDUCATIVA_ACREEDOR_NIT='900000000-1',
    FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL='REPRESENTANTE PRUEBA',
    FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO='Bogota D.C.',
    FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA='1',
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION=(
        'CLAUSULA DE OBLIGACION EDUCATIVA APROBADA PARA PRUEBAS.'
    ),
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES=(
        'CLAUSULA DE CARTA DE INSTRUCCIONES APROBADA PARA PRUEBAS.'
    ),
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO=(
        'CLAUSULA DE INCUMPLIMIENTO APROBADA PARA PRUEBAS.'
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

    def test_genera_dos_pdfs_privados_versionados_e_idempotentes(self):
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
        self.assertNotIn('desembolso', texto_pagare.lower())
        self.assertIn('CARTA DE INSTRUCCIONES', texto_pagare)
        self.assertEqual(
            primera.pagare.version_plantilla,
            'PAGARE-2.0-EDU-1',
        )
        self.assertIn('FICHA DE MATRÍCULA', texto_ficha)
        self.assertIn('Información de Matrícula', texto_ficha)
        self.assertIn('Información de Retiro', texto_ficha)
        self.assertNotIn('38557506', texto_ficha)
        with primera.ficha_matricula.archivo.open('rb') as archivo:
            lector = PdfReader(archivo)
            self.assertEqual(len(lector.pages), 1)
            self.assertGreaterEqual(len(lector.pages[0].images), 1)

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

        self.client.force_login(self.otro_usuario)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        respuesta.close()

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
