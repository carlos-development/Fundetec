import os
import re
import stat
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from instituciones.authentication import InstitutionApiKeyAuthentication
from instituciones.admin import CredencialAPIInstitucionAdmin
from instituciones.models import CredencialAPIInstitucion, Institucion
from instituciones.services.credenciales import crear_credencial_api


class ComandosCredencialesInstitucionalesTests(TestCase):
    def setUp(self):
        self.institucion = Institucion.objects.create(
            nombre_comercial='Institucion Comandos',
            razon_social='Institucion Comandos SAS',
            numero_identificacion_tributaria='901000099',
        )

    def _emitir_visible(self, nombre='Personal QA', prefijo=None):
        salida = StringIO()
        opciones = {
            'institucion_id': str(self.institucion.id),
            'nombre': nombre,
            'mostrar_token': True,
            'stdout': salida,
        }
        if prefijo is not None:
            opciones['prefijo'] = prefijo
        call_command('emitir_credencial_institucional', **opciones)
        contenido = salida.getvalue()
        coincidencia = re.search(r'^TOKEN_UNICA_VEZ=(.+)$', contenido, re.MULTILINE)
        self.assertIsNotNone(coincidencia)
        return contenido, coincidencia.group(1)

    def test_emite_token_una_sola_vez_y_solo_persiste_hash(self):
        salida, token = self._emitir_visible()
        credencial = CredencialAPIInstitucion.objects.get()
        prefijo, secreto = token.split('.', 1)

        self.assertEqual(salida.count(token), 1)
        self.assertEqual(prefijo, credencial.prefijo_clave)
        self.assertTrue(credencial.verificar_secreto(secreto))
        self.assertNotIn(secreto, credencial.secreto_hash)
        self.assertNotIn(credencial.secreto_hash, salida)

    def test_prefijo_personalizado_se_normaliza_y_autentica(self):
        _, token = self._emitir_visible(
            nombre='Ingles',
            prefijo='  FUNDETEC_Ingles  ',
        )
        credencial = CredencialAPIInstitucion.objects.get()

        self.assertEqual(credencial.prefijo_clave, 'fundetec_ingles')
        self.assertTrue(token.startswith('fundetec_ingles.'))

        request = APIRequestFactory().get(
            '/',
            HTTP_AUTHORIZATION=f'ApiKey {token}',
        )
        institucion, autenticada = InstitutionApiKeyAuthentication().authenticate(
            request
        )
        self.assertEqual(institucion, self.institucion)
        self.assertEqual(autenticada, credencial)

    def test_prefijo_personalizado_duplicado_se_rechaza(self):
        self._emitir_visible(nombre='Primera', prefijo='fundetec_ingles')

        with self.assertRaisesMessage(CommandError, 'prefijo ya esta en uso'):
            self._emitir_visible(
                nombre='Segunda',
                prefijo=' FUNDETEC_INGLES ',
            )

        self.assertEqual(CredencialAPIInstitucion.objects.count(), 1)

    def test_prefijos_personalizados_invalidos_se_rechazan(self):
        invalidos = (
            '',
            'fundetec.ingles',
            'fundetec ingles',
            'fundetec/ingles',
            'fundetec_espa\u00f1ol',
            'a' * 17,
        )

        for indice, prefijo in enumerate(invalidos):
            with self.subTest(prefijo=prefijo):
                with self.assertRaises(CommandError):
                    self._emitir_visible(
                        nombre=f'Invalida {indice}',
                        prefijo=prefijo,
                    )

        self.assertFalse(CredencialAPIInstitucion.objects.exists())

    def test_sin_prefijo_conserva_generacion_automatica(self):
        _, token = self._emitir_visible(nombre='Automatica')
        credencial = CredencialAPIInstitucion.objects.get()

        self.assertRegex(credencial.prefijo_clave, r'^[0-9a-f]{16}$')
        self.assertTrue(token.startswith(f'{credencial.prefijo_clave}.'))

    def test_token_emitido_por_comando_autentica_en_el_backend_real(self):
        _, token = self._emitir_visible(nombre='Backend real')
        request = APIRequestFactory().get(
            '/',
            HTTP_AUTHORIZATION=f'ApiKey {token}',
        )

        institucion, credencial = InstitutionApiKeyAuthentication().authenticate(
            request
        )

        self.assertEqual(institucion, self.institucion)
        self.assertEqual(credencial.institucion, self.institucion)

    def test_archivo_token_es_exclusivo_y_no_filtra_token_en_stdout(self):
        with TemporaryDirectory() as directorio:
            if os.name == 'posix':
                os.chmod(directorio, 0o700)
            ruta = Path(directorio) / 'credencial.token'
            salida = StringIO()

            call_command(
                'emitir_credencial_institucional',
                institucion_id=str(self.institucion.id),
                nombre='Archivo seguro',
                archivo_token=str(ruta),
                stdout=salida,
            )

            token = ruta.read_text(encoding='utf-8').strip()
            credencial = CredencialAPIInstitucion.objects.get()
            self.assertNotIn(token, salida.getvalue())
            self.assertTrue(credencial.verificar_secreto(token.split('.', 1)[1]))
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(ruta.stat().st_mode), 0o600)

    def test_no_sobrescribe_archivo_y_no_crea_credencial(self):
        with TemporaryDirectory() as directorio:
            if os.name == 'posix':
                os.chmod(directorio, 0o700)
            ruta = Path(directorio) / 'existente.token'
            ruta.write_text('NO-TOCAR', encoding='utf-8')

            with self.assertRaises(CommandError):
                call_command(
                    'emitir_credencial_institucional',
                    institucion_id=str(self.institucion.id),
                    nombre='No creada',
                    archivo_token=str(ruta),
                )

            self.assertEqual(ruta.read_text(encoding='utf-8'), 'NO-TOCAR')
            self.assertFalse(CredencialAPIInstitucion.objects.exists())

    def test_listado_no_expone_token_ni_hash(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Listado',
        )
        salida = StringIO()

        call_command(
            'listar_credenciales_institucionales',
            stdout=salida,
        )

        contenido = salida.getvalue()
        self.assertIn(str(emitida.credencial.id), contenido)
        self.assertIn(emitida.credencial.prefijo_clave, contenido)
        self.assertNotIn(emitida.token, contenido)
        self.assertNotIn(emitida.token.split('.', 1)[1], contenido)
        self.assertNotIn(emitida.credencial.secreto_hash, contenido)

    def test_rotacion_requiere_confirmacion_e_invalida_token_anterior(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Rotacion',
        )
        secreto_anterior = emitida.token.split('.', 1)[1]

        with self.assertRaisesMessage(CommandError, '--confirmar'):
            call_command(
                'rotar_credencial_institucional',
                credencial_id=str(emitida.credencial.id),
                mostrar_token=True,
            )

        salida = StringIO()
        call_command(
            'rotar_credencial_institucional',
            credencial_id=str(emitida.credencial.id),
            confirmar=True,
            mostrar_token=True,
            stdout=salida,
        )
        token_nuevo = re.search(
            r'^TOKEN_UNICA_VEZ=(.+)$',
            salida.getvalue(),
            re.MULTILINE,
        ).group(1)
        emitida.credencial.refresh_from_db()

        self.assertFalse(emitida.credencial.verificar_secreto(secreto_anterior))
        self.assertTrue(
            emitida.credencial.verificar_secreto(token_nuevo.split('.', 1)[1])
        )

    def test_fallo_de_archivo_en_rotacion_conserva_secreto_anterior(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Rotacion protegida',
        )
        secreto_anterior = emitida.token.split('.', 1)[1]

        with TemporaryDirectory() as directorio:
            if os.name == 'posix':
                os.chmod(directorio, 0o700)
            ruta = Path(directorio) / 'existente.token'
            ruta.write_text('NO-TOCAR', encoding='utf-8')

            with self.assertRaises(CommandError):
                call_command(
                    'rotar_credencial_institucional',
                    credencial_id=str(emitida.credencial.id),
                    confirmar=True,
                    archivo_token=str(ruta),
                )

        emitida.credencial.refresh_from_db()
        self.assertTrue(emitida.credencial.verificar_secreto(secreto_anterior))

    def test_revocacion_es_confirmada_e_idempotente(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Revocable',
        )

        with self.assertRaisesMessage(CommandError, '--confirmar'):
            call_command(
                'revocar_credencial_institucional',
                credencial_id=str(emitida.credencial.id),
            )

        primera = StringIO()
        call_command(
            'revocar_credencial_institucional',
            credencial_id=str(emitida.credencial.id),
            confirmar=True,
            stdout=primera,
        )
        segunda = StringIO()
        call_command(
            'revocar_credencial_institucional',
            credencial_id=str(emitida.credencial.id),
            confirmar=True,
            stdout=segunda,
        )

        emitida.credencial.refresh_from_db()
        self.assertFalse(emitida.credencial.activa)
        self.assertIn('ESTADO=REVOCADA', primera.getvalue())
        self.assertIn('ESTADO=YA_REVOCADA', segunda.getvalue())
        self.assertNotIn(emitida.credencial.secreto_hash, primera.getvalue())

    def test_lista_instituciones_sin_credenciales(self):
        salida = StringIO()
        call_command('listar_instituciones_api', stdout=salida)

        contenido = salida.getvalue()
        self.assertIn(str(self.institucion.id), contenido)
        self.assertIn(self.institucion.nombre_comercial, contenido)
        self.assertNotIn('secreto', contenido.lower())

    def test_admin_no_expone_hash_y_es_solo_lectura(self):
        modelo_admin = CredencialAPIInstitucionAdmin(
            CredencialAPIInstitucion,
            AdminSite(),
        )

        self.assertIn('secreto_hash', modelo_admin.exclude)
        self.assertFalse(modelo_admin.has_add_permission(None))
        self.assertFalse(modelo_admin.has_change_permission(None))
        self.assertFalse(modelo_admin.has_delete_permission(None))
