from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings
from django.utils.module_loading import import_string


MANIFEST_BACKEND = (
    'whitenoise.storage.CompressedManifestStaticFilesStorage'
)


class StaticfilesVersioningTests(SimpleTestCase):
    def test_default_preserva_almacenamiento_de_archivos(self):
        from django.conf import settings

        self.assertEqual(
            settings.STORAGES['default']['BACKEND'],
            'django.core.files.storage.FileSystemStorage',
        )

    def test_collectstatic_genera_manifest_assets_versionados_y_template(self):
        with TemporaryDirectory() as destino:
            asset_historico = (
                Path(destino)
                / 'js'
                / 'financiacion_educativa_camara.historico.js'
            )
            asset_historico.parent.mkdir(parents=True)
            asset_historico.write_text('asset historico', encoding='utf-8')
            with override_settings(
                DEBUG=False,
                STATIC_ROOT=destino,
                STORAGES={
                    'default': {
                        'BACKEND': 'django.core.files.storage.FileSystemStorage',
                    },
                    'staticfiles': {'BACKEND': MANIFEST_BACKEND},
                },
            ):
                call_command('collectstatic', interactive=False, verbosity=0)

                manifest = Path(destino) / 'staticfiles.json'
                css_url = staticfiles_storage.url(
                    'css/financiacion_educativa.css'
                )
                js_url = staticfiles_storage.url(
                    'js/financiacion_educativa_camara.js'
                )
                renderizado = Template(
                    "{% load static %}{% static 'js/financiacion_educativa_camara.js' %}"
                ).render(Context())

                self.assertTrue(manifest.is_file())
                self.assertRegex(
                    css_url,
                    r'/static/css/financiacion_educativa\.[0-9a-f]{12}\.css$',
                )
                self.assertRegex(
                    js_url,
                    r'/static/js/financiacion_educativa_camara\.[0-9a-f]{12}\.js$',
                )
                self.assertEqual(renderizado, js_url)
                self.assertTrue(
                    (Path(destino) / css_url.removeprefix('/static/')).is_file()
                )
                self.assertTrue(
                    (Path(destino) / js_url.removeprefix('/static/')).is_file()
                )
                self.assertTrue(asset_historico.is_file())

    def test_hash_cambia_con_el_contenido(self):
        with TemporaryDirectory() as destino:
            storage = import_string(MANIFEST_BACKEND)(
                location=destino,
                base_url='/static/',
            )
            primero = storage.hashed_name(
                'css/probe.css',
                content=ContentFile(b'body { color: #000; }'),
            )
            segundo = storage.hashed_name(
                'css/probe.css',
                content=ContentFile(b'body { color: #111; }'),
            )

        self.assertNotEqual(primero, segundo)

    @override_settings(
        DEBUG=True,
        STORAGES={
            'default': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
            },
            'staticfiles': {
                'BACKEND': (
                    'django.contrib.staticfiles.storage.StaticFilesStorage'
                ),
            },
        },
    )
    def test_desarrollo_no_exige_manifest_preexistente(self):
        with TemporaryDirectory() as destino:
            with override_settings(STATIC_ROOT=destino):
                url = Template(
                    "{% load static %}{% static 'js/financiacion_educativa_camara.js' %}"
                ).render(Context())

        self.assertEqual(url, '/static/js/financiacion_educativa_camara.js')
