from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from financiacion_educativa.services.cola_automatizacion import ejecutar_worker


SYSTEMD_ROOT = Path(settings.BASE_DIR) / 'deploy' / 'systemd'


class SystemdStagingUnitTests(SimpleTestCase):
    units = {
        'fundetec-staging-educational-worker.service': (
            'manage.py procesar_cola_educativa'
        ),
        'fundetec-staging-email-outbox.service': (
            'manage.py procesar_outbox_educativo'
        ),
    }

    def test_units_use_expected_identity_environment_and_hardening(self):
        required = {
            'User=fundetec-staging',
            'Group=fundetec-staging',
            'WorkingDirectory=/var/www/fundetec-staging/current',
            'EnvironmentFile=/var/www/fundetec-staging/shared/staging.env',
            'Restart=on-failure',
            'RestartSec=5',
            'NoNewPrivileges=true',
            'PrivateTmp=true',
            'ProtectSystem=strict',
            'ProtectHome=true',
        }
        for filename, command in self.units.items():
            with self.subTest(unit=filename):
                content = (SYSTEMD_ROOT / filename).read_text(encoding='ascii')
                lines = set(content.splitlines())
                self.assertTrue(required.issubset(lines))
                self.assertIn(
                    'ExecStart=/var/www/fundetec-staging/shared/venv/bin/'
                    f'python {command}',
                    lines,
                )
                self.assertNotIn('--once', content)
                self.assertNotIn('--limit', content)

    def test_automation_worker_without_limits_remains_continuous(self):
        with patch(
            'financiacion_educativa.services.cola_automatizacion.'
            'procesar_siguiente_trabajo',
            side_effect=[SimpleNamespace(procesado=False), KeyboardInterrupt],
        ) as process, patch(
            'financiacion_educativa.services.cola_automatizacion.time.sleep'
        ) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                ejecutar_worker()

        self.assertEqual(process.call_count, 2)
        sleep.assert_called_once_with(2)

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True)
    def test_outbox_command_without_limits_remains_continuous(self):
        with patch(
            'financiacion_educativa.management.commands.'
            'procesar_outbox_educativo.procesar_siguiente_correo',
            side_effect=[SimpleNamespace(procesado=False), KeyboardInterrupt],
        ) as process, patch(
            'financiacion_educativa.management.commands.'
            'procesar_outbox_educativo.time.sleep'
        ) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command('procesar_outbox_educativo')

        self.assertEqual(process.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_commands_do_not_override_process_signal_handling(self):
        paths = (
            Path(settings.BASE_DIR) / 'financiacion_educativa' / 'services'
            / 'cola_automatizacion.py',
            Path(settings.BASE_DIR) / 'financiacion_educativa' / 'management'
            / 'commands' / 'procesar_outbox_educativo.py',
        )
        for path in paths:
            with self.subTest(path=path.name):
                content = path.read_text(encoding='utf-8')
                self.assertNotIn('signal.signal', content)
                self.assertNotIn('SIGTERM', content)
