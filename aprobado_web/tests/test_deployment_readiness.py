import json
import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase, TestCase
from django.urls import reverse


BASE_DIR = Path(__file__).resolve().parents[2]


class HealthCheckTests(TestCase):
    def test_health_check_es_publico_minimo_y_no_consulta_base(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_health_check_admite_head_y_rechaza_post(self):
        self.assertEqual(self.client.head(reverse('health')).status_code, 200)
        self.assertEqual(self.client.post(reverse('health')).status_code, 405)


class StagingSettingsTests(SimpleTestCase):
    maxDiff = None

    def _staging_environment(self):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {
                'SECRET_KEY',
                'DATABASE_URL',
                'USE_SQLITE',
                'ALLOWED_HOSTS',
                'CSRF_TRUSTED_ORIGINS',
                'STATIC_ROOT',
                'MEDIA_ROOT',
                'FINANCIACION_EDUCATIVA_PRIVATE_ROOT',
                'EMAIL_BACKEND',
                'EMAIL_QA_MODE',
                'EMAIL_LIVE_DELIVERY_ENABLED',
                'EMAIL_QA_REDIRECT_TO',
                'FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS',
            }
        }
        environment.update(
            {
                'DJANGO_SETTINGS_MODULE': 'aprobado_web.settings',
                'DJANGO_LOAD_DOTENV': 'false',
                'DEPLOYMENT_ENVIRONMENT': 'staging',
                'DEBUG': 'false',
                'SECRET_KEY': 'test-only-secret-key-with-more-than-32-characters',
                'USE_SQLITE': 'false',
                'DATABASE_URL': (
                    'postgresql://test:test@127.0.0.1:5432/fundetec_test'
                ),
                'ALLOWED_HOSTS': 'staging-api.aprobado.com.co',
                'CSRF_TRUSTED_ORIGINS': (
                    'https://staging-api.aprobado.com.co'
                ),
                'STATIC_ROOT': (
                    '/var/www/fundetec-staging/shared/staticfiles'
                ),
                'MEDIA_ROOT': '/var/www/fundetec-staging/shared/media',
                'FINANCIACION_EDUCATIVA_PRIVATE_ROOT': (
                    '/var/www/fundetec-staging/shared/private'
                ),
                'FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY': (
                    'test-only-invitation-hmac-key'
                ),
                'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY': (
                    'test-only-mobile-capture-hmac-key'
                ),
                'EMAIL_BACKEND': (
                    'aprobado_web.email_backends.SafeRoutingEmailBackend'
                ),
                'EMAIL_QA_MODE': 'true',
                'EMAIL_LIVE_DELIVERY_ENABLED': 'false',
                'EMAIL_QA_REDIRECT_TO': 'qa@example.test',
                'EMAIL_HOST': 'smtp.example.test',
                'EMAIL_PORT': '587',
                'EMAIL_USE_TLS': 'true',
                'EMAIL_USE_SSL': 'false',
                'EMAIL_HOST_USER': 'noreply@example.test',
                'EMAIL_HOST_PASSWORD': 'test-only-smtp-password',
                'DEFAULT_FROM_EMAIL': 'Aprobado <noreply@example.test>',
            }
        )
        return environment

    def _load_settings(self, environment):
        code = """
import json
from django.conf import settings
print(json.dumps({
    'database_engine': settings.DATABASES['default']['ENGINE'],
    'static_root': str(settings.STATIC_ROOT).replace('\\\\', '/'),
    'media_root': str(settings.MEDIA_ROOT).replace('\\\\', '/'),
    'private_root': str(settings.FINANCIACION_EDUCATIVA_PRIVATE_ROOT).replace('\\\\', '/'),
    'allowed_hosts': settings.ALLOWED_HOSTS,
    'csrf_origins': settings.CSRF_TRUSTED_ORIGINS,
    'email_backend': settings.EMAIL_BACKEND,
}))
"""
        return subprocess.run(
            [sys.executable, '-c', code],
            cwd=BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_staging_carga_postgresql_rutas_y_correo_qa(self):
        result = self._load_settings(self._staging_environment())

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['database_engine'], 'django.db.backends.postgresql')
        self.assertEqual(
            data['static_root'],
            '/var/www/fundetec-staging/shared/staticfiles',
        )
        self.assertEqual(
            data['media_root'],
            '/var/www/fundetec-staging/shared/media',
        )
        self.assertEqual(
            data['private_root'],
            '/var/www/fundetec-staging/shared/private',
        )
        self.assertEqual(data['allowed_hosts'], ['staging-api.aprobado.com.co'])
        self.assertEqual(
            data['csrf_origins'],
            ['https://staging-api.aprobado.com.co'],
        )
        self.assertEqual(
            data['email_backend'],
            'aprobado_web.email_backends.SafeRoutingEmailBackend',
        )

    def test_staging_rechaza_secret_key_ausente(self):
        environment = self._staging_environment()
        environment.pop('SECRET_KEY')

        result = self._load_settings(environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY debe definirse externamente', result.stderr)
        self.assertNotIn('test-only-smtp-password', result.stderr)

    def test_staging_rechaza_database_url_ausente_y_sqlite(self):
        environment = self._staging_environment()
        environment.pop('DATABASE_URL')
        missing_database = self._load_settings(environment)

        environment = self._staging_environment()
        environment['USE_SQLITE'] = 'true'
        sqlite_database = self._load_settings(environment)

        self.assertNotEqual(missing_database.returncode, 0)
        self.assertIn('DATABASE_URL es obligatorio', missing_database.stderr)
        self.assertNotEqual(sqlite_database.returncode, 0)
        self.assertIn('SQLite no esta permitido', sqlite_database.stderr)

    def test_staging_rechaza_hosts_y_rutas_no_definidos(self):
        environment = self._staging_environment()
        environment.pop('ALLOWED_HOSTS')
        missing_hosts = self._load_settings(environment)

        environment = self._staging_environment()
        environment.pop('STATIC_ROOT')
        missing_static_root = self._load_settings(environment)

        self.assertNotEqual(missing_hosts.returncode, 0)
        self.assertIn('ALLOWED_HOSTS debe definirse', missing_hosts.stderr)
        self.assertNotEqual(missing_static_root.returncode, 0)
        self.assertIn('STATIC_ROOT es obligatorio', missing_static_root.stderr)

    def test_staging_rechaza_correo_sin_modo_explicito(self):
        environment = self._staging_environment()
        environment['EMAIL_QA_MODE'] = 'false'
        environment['EMAIL_LIVE_DELIVERY_ENABLED'] = 'false'
        delivery_disabled = self._load_settings(environment)

        environment = self._staging_environment()
        environment['EMAIL_BACKEND'] = (
            'django.core.mail.backends.smtp.EmailBackend'
        )
        unsafe_backend = self._load_settings(environment)

        self.assertNotEqual(delivery_disabled.returncode, 0)
        self.assertIn(
            'Staging requiere EMAIL_QA_MODE=True o la habilitacion explicita',
            delivery_disabled.stderr,
        )
        self.assertNotEqual(unsafe_backend.returncode, 0)
        self.assertIn('Staging requiere SafeRoutingEmailBackend', unsafe_backend.stderr)

    def test_staging_admite_entrega_real_explicita_con_notificacion_operativa(self):
        environment = self._staging_environment()
        environment['EMAIL_QA_MODE'] = 'false'
        environment['EMAIL_LIVE_DELIVERY_ENABLED'] = 'true'
        environment['FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS'] = (
            'soporte@example.test'
        )

        result = self._load_settings(environment)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_rechaza_qa_y_entrega_real_simultaneos(self):
        environment = self._staging_environment()
        environment['EMAIL_LIVE_DELIVERY_ENABLED'] = 'true'

        result = self._load_settings(environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            'EMAIL_QA_MODE y EMAIL_LIVE_DELIVERY_ENABLED no pueden estar activos',
            result.stderr,
        )
