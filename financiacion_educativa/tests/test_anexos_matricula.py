import hashlib
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.template.loader import render_to_string
from pypdf import PdfReader

from financiacion_educativa.admin_anexos import AnexoInstitucionalAdmin
from financiacion_educativa.models import (
    ArtefactoContractualEducativo, ConfiguracionAnexoMatriculaEducativa,
    ProcesoFirmaEducativa,
)
from financiacion_educativa.services import artefactos_contractuales as contratos
from financiacion_educativa.services.anexos_matricula import (
    AnexoMatriculaError, datos_jowalth, resolver_anexo,
)
from financiacion_educativa.services.firma_zapsign import FirmaEducativaError, enviar_pagare_educativo
from financiacion_educativa.tests import test_artefactos_contractuales as fixtures
from financiacion_educativa.tests.factories import crear_institucion, crear_solicitud
from financiacion_educativa.tests.signature_backends import RecordingEducationalSignatureBackend as Backend


@override_settings(**{
    **fixtures.ArtefactosContractualesTests._overridden_settings,
    'DEPLOYMENT_ENVIRONMENT': 'test',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED': False,
    'FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED': False,
    'FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND': 'financiacion_educativa.tests.signature_backends.RecordingEducationalSignatureBackend',
    'FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS': True,
    'FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY': 'synthetic-annex-test-key',
})
class AnexosMatriculaTests(TestCase):
    _participante = fixtures.ArtefactosContractualesTests._participante
    _preparar_finanzas = fixtures.ArtefactosContractualesTests._preparar_finanzas
    _texto_pdf = fixtures.ArtefactosContractualesTests._texto_pdf

    def setUp(self):
        fixtures.ArtefactosContractualesTests.setUp(self)
        Backend.reset()
        self.config = ConfiguracionAnexoMatriculaEducativa.objects.get(pk=self.solicitud.institucion_id)

    def preparar(self, *, jowalth=True, menor=False):
        if jowalth:
            self.config.perfil = 'JOWALTH_ACADEMY'
            self.config.save()
        self._participante(menor=menor)
        if menor:
            self._participante(tutor=True)
        self._preparar_finanzas()

    def generar(self):
        return contratos.generar_artefactos_contractuales(solicitud=self.solicitud)

    def contenido(self, artefacto):
        with artefacto.archivo.open('rb') as archivo:
            return archivo.read()

    def test_jowalth_cuatro_paginas_paquete_siete_y_cuerpo_inalterado(self):
        self.preparar()
        with patch.object(contratos, '_renderizar_pdf', wraps=contratos._renderizar_pdf) as render:
            resultado = self.generar()
        paquete = PdfReader(BytesIO(self.contenido(resultado.pagare)))
        ficha = PdfReader(BytesIO(self.contenido(resultado.ficha_matricula)))
        self.assertEqual(len(ficha.pages), 4)
        self.assertEqual(len(paquete.pages), 7)
        contexto = render.call_args_list[0].args[1]
        original = PdfReader(BytesIO(contratos._renderizar_pdf(
            'financiacion_educativa/documentos/paquete_contractual_v3.html',
            {**contexto, 'omitir_anexo_matricula': False},
        )))
        for indice in range(3):
            self.assertEqual(paquete.pages[indice].extract_text(), original.pages[indice].extract_text())
        for indice in range(4):
            self.assertEqual(paquete.pages[indice + 3].extract_text(), ficha.pages[indice].extract_text())
        for artefacto in (resultado.pagare, resultado.ficha_matricula):
            self.assertEqual(artefacto.anexo_matricula_snapshot, {
                'institution_id': str(self.solicitud.institucion_id),
                'profile': 'JOWALTH_ACADEMY', 'version': 'JOW-MAT-1.0',
            })
            self.assertEqual(artefacto.hash_sha256, hashlib.sha256(self.contenido(artefacto)).hexdigest())
        texto = self._texto_pdf(resultado.ficha_matricula)
        self.assertIn('ESTUDIANTE', texto)
        self.assertNotIn('FUNDETEC', texto.upper())
        self.assertEqual(resultado.ficha_matricula.version_plantilla, 'JOW-MAT-1.0')
        # Optional exports contain synthetic fixtures only and stay outside the repository.
        if os.environ.get('EDU_ANNEX_SAMPLES_DIR'):
            import pypdfium2
            destino = Path(os.environ['EDU_ANNEX_SAMPLES_DIR'])
            destino.mkdir(parents=True, exist_ok=True)
            for nombre, artefacto in [('jowalth-ficha', resultado.ficha_matricula), ('jowalth-paquete', resultado.pagare)]:
                pdf = self.contenido(artefacto)
                (destino / f'{nombre}.pdf').write_bytes(pdf)
                documento = pypdfium2.PdfDocument(pdf)
                for indice in range(len(documento)):
                    pagina = documento[indice]
                    bitmap = pagina.render(scale=1.5)
                    bitmap.to_pil().save(destino / f'{nombre}-{indice + 1}.png')
                    bitmap.close()
                    pagina.close()
                documento.close()

    def test_fundetec_conserva_cuatro_y_una_paginas(self):
        self.preparar(jowalth=False)
        resultado = self.generar()
        self.assertEqual(len(PdfReader(BytesIO(self.contenido(resultado.pagare))).pages), 4)
        self.assertEqual(len(PdfReader(BytesIO(self.contenido(resultado.ficha_matricula))).pages), 1)
        self.assertEqual(resultado.ficha_matricula.version_plantilla, 'FO-AD-005-V2-EDU-2')

    def test_seleccion_por_identificador_y_aislamiento(self):
        self.preparar()
        institucion = self.solicitud.institucion
        institucion.nombre_comercial = 'Nombre cambiado para prueba'
        institucion.save()
        otra = crear_solicitud(institucion=crear_institucion('2'))
        self.assertEqual(resolver_anexo(self.solicitud).codigo, 'JOWALTH_ACADEMY')
        self.assertEqual(resolver_anexo(otra).codigo, 'FUNDETEC_FO_AD_005')

    def test_anexo_ausente_inactivo_o_desconocido_bloquea_sin_alternativa(self):
        self.preparar()
        for valor in (False, True):
            self.config.activa = valor
            self.config.save()
            if valor:
                ConfiguracionAnexoMatriculaEducativa.objects.filter(pk=self.config.pk).update(perfil='NO_EXISTE')
            with self.assertRaises(AnexoMatriculaError), patch.object(contratos, '_renderizar_pdf') as render:
                self.generar()
            render.assert_not_called()
        self.config.delete()
        with self.assertRaises(AnexoMatriculaError):
            self.generar()
        self.assertFalse(ArtefactoContractualEducativo.objects.exists())
        self.assertFalse(ProcesoFirmaEducativa.objects.exists())

    def test_error_generacion_no_persiste_paquete_parcial(self):
        self.preparar()
        real = contratos._renderizar_pdf
        def render(plantilla, contexto):
            if 'anexo_matricula_jowalth' in plantilla:
                raise RuntimeError('Synthetic render failure')
            return real(plantilla, contexto)
        with patch.object(contratos, '_renderizar_pdf', side_effect=render), self.assertRaises(AnexoMatriculaError) as error:
            self.generar()
        self.assertEqual(error.exception.codigo, 'ENROLLMENT_ANNEX_GENERATION_FAILED')
        self.assertFalse(ArtefactoContractualEducativo.objects.exists())
        self.assertFalse(ProcesoFirmaEducativa.objects.exists())

    def test_anexo_incompleto_bloquea(self):
        self.preparar()
        real = contratos._renderizar_pdf
        def render(plantilla, contexto):
            return real('financiacion_educativa/documentos/ficha_matricula_pdf.html', contexto) if 'anexo_matricula_jowalth' in plantilla else real(plantilla, contexto)
        with patch.object(contratos, '_renderizar_pdf', side_effect=render), self.assertRaises(AnexoMatriculaError):
            self.generar()
        self.assertFalse(ArtefactoContractualEducativo.objects.exists())

    def test_configuracion_cambia_durante_render_no_persiste(self):
        self.preparar()
        real = contratos._renderizar_pdf
        def render(plantilla, contexto):
            pdf = real(plantilla, contexto)
            self.config.activa = False
            self.config.save()
            return pdf
        with patch.object(contratos, '_renderizar_pdf', side_effect=render), self.assertRaises(AnexoMatriculaError):
            self.generar()
        self.assertFalse(ArtefactoContractualEducativo.objects.exists())

    def test_reintento_no_regenera_y_envia_exactamente_bytes_persistidos(self):
        self.preparar()
        primero = self.generar()
        with patch.object(contratos, '_renderizar_pdf') as render:
            segundo = self.generar()
        render.assert_not_called()
        self.assertEqual(primero.pagare.pk, segundo.pagare.pk)
        proceso = ProcesoFirmaEducativa.objects.get(artefacto=primero.pagare)
        self.config.activa = False
        self.config.save()
        with self.assertRaises(AnexoMatriculaError):
            enviar_pagare_educativo(proceso=proceso)
        proceso.refresh_from_db()
        self.assertEqual(proceso.estado, 'FAILED')
        self.assertEqual(proceso.codigo_ultimo_error, 'ENROLLMENT_ANNEX_NOT_READY')
        self.assertEqual(Backend.submissions, [])
        self.config.activa = True
        self.config.save()
        enviar_pagare_educativo(proceso=proceso)
        self.assertEqual(Backend.submissions[0]['pdf'], self.contenido(primero.pagare))
        self.assertEqual(ArtefactoContractualEducativo.objects.count(), 2)

    def test_historico_jowalth_sin_metadatos_no_se_regenera_ni_envia(self):
        self.preparar()
        resultado = self.generar()
        original = self.contenido(resultado.pagare)
        ArtefactoContractualEducativo.objects.update(anexo_matricula_snapshot={})
        with self.assertRaises(AnexoMatriculaError), patch.object(contratos, '_renderizar_pdf') as render:
            self.generar()
        render.assert_not_called()
        proceso = ProcesoFirmaEducativa.objects.get(artefacto=resultado.pagare)
        with self.assertRaises(AnexoMatriculaError):
            enviar_pagare_educativo(proceso=proceso)
        self.assertEqual(Backend.submissions, [])
        self.assertEqual(self.contenido(resultado.pagare), original)

    def test_envio_historico_sent_signed_sigue_idempotente_sin_configuracion(self):
        self.preparar()
        resultado = self.generar()
        original = self.contenido(resultado.pagare)
        proceso = ProcesoFirmaEducativa.objects.get(artefacto=resultado.pagare)
        self.config.activa = False
        self.config.save()
        for estado in ('SENT', 'SIGNED'):
            ProcesoFirmaEducativa.objects.filter(pk=proceso.pk).update(estado=estado)
            self.assertEqual(enviar_pagare_educativo(proceso=proceso).estado, estado)
        self.assertEqual(Backend.submissions, [])
        self.assertEqual(self.contenido(resultado.pagare), original)

    def test_integridad_bloquea_envio_y_perfil_versionado_no_se_cambia(self):
        self.preparar()
        resultado = self.generar()
        self.config.perfil = 'FUNDETEC_FO_AD_005'
        with self.assertRaises(ValidationError):
            self.config.save()
        with resultado.pagare.archivo.open('wb') as archivo:
            archivo.write(b'%PDF-modificado-sintetico')
        proceso = ProcesoFirmaEducativa.objects.get(artefacto=resultado.pagare)
        with self.assertRaises(FirmaEducativaError):
            enviar_pagare_educativo(proceso=proceso)
        proceso.refresh_from_db()
        self.assertEqual(proceso.codigo_ultimo_error, 'CONTRACT_FILE_INTEGRITY_FAILED')
        self.assertEqual(Backend.submissions, [])

    def test_menor_tutor_datos_disponibles_sin_casillas_o_firmas_aceptadas(self):
        self.preparar(menor=True)
        resultado = self.generar()
        texto = self._texto_pdf(resultado.ficha_matricula)
        self.assertIn('TUTOR RESPONSABLE', texto)
        self.assertIn('ESTUDIANTE', texto)
        self.assertIn('TUTOR RESPONSABLE', self._texto_pdf(resultado.pagare))
        html = render_to_string('financiacion_educativa/documentos/anexo_matricula_jowalth_v1.html', {
            'jowalth': datos_jowalth(self.solicitud), 'anexo_version': 'JOW-MAT-1.0',
        })
        firmas = html.split('<table class="signatures">')[1]
        self.assertNotIn('TUTOR RESPONSABLE', firmas)
        self.assertNotIn('ESTUDIANTE EDUCATIVO', firmas)
        self.assertNotIn('checked', html)
        self.assertIsNone(datos_jowalth(self.solicitud)['fecha_matricula'])

    def test_configuracion_admin_exige_permiso_explicito(self):
        from django.contrib import admin
        from django.contrib.auth.models import Permission
        model_admin = AnexoInstitucionalAdmin(ConfiguracionAnexoMatriculaEducativa, admin.site)
        self.usuario.is_staff = True
        self.usuario.save()
        request = RequestFactory().get('/admin/')
        request.user = self.usuario
        self.assertFalse(model_admin.has_change_permission(request))
        self.usuario.user_permissions.add(Permission.objects.get(codename='gestionar_anexos_matricula'))
        request.user = type(self.usuario).objects.get(pk=self.usuario.pk)
        self.assertTrue(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertEqual(model_admin.version_anexo(None), 'Sin configurar')


@override_settings(**AnexosMatriculaTests._overridden_settings)
class AnexosMatriculaConcurrenciaTests(TransactionTestCase):
    setUp = AnexosMatriculaTests.setUp
    preparar = AnexosMatriculaTests.preparar
    _participante = AnexosMatriculaTests._participante
    _preparar_finanzas = AnexosMatriculaTests._preparar_finanzas

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_generadores_conservan_un_paquete_y_una_ficha(self):
        self.preparar()
        barrera = Barrier(2)
        render_real = contratos._renderizar_pdf

        def render(plantilla, contexto):
            pdf = render_real(plantilla, contexto)
            if 'anexo_matricula_jowalth' in plantilla:
                barrera.wait(timeout=30)
            return pdf

        def generar():
            close_old_connections()
            conexion = connections['default']
            try:
                with conexion.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                resultado = contratos.generar_artefactos_contractuales(solicitud=self.solicitud)
                return resultado.pagare.pk, resultado.ficha_matricula.pk, pid
            finally:
                conexion.close()

        with patch.object(contratos, '_renderizar_pdf', side_effect=render), ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(generar) for _ in range(2)]
            resultados = [futuro.result(timeout=90) for futuro in futuros]
        self.assertEqual(resultados[0][:2], resultados[1][:2])
        self.assertNotEqual(resultados[0][2], resultados[1][2])
        self.assertEqual(ArtefactoContractualEducativo.objects.count(), 2)
        self.assertEqual(ProcesoFirmaEducativa.objects.count(), 1)
        self.assertTrue(connections['default'].is_usable())
