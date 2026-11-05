import configparser
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest import TestCase


class ProductionReceiptsUnitTests(TestCase):
    def setUp(self):
        ruta = Path(__file__).resolve().parents[2] / 'deploy/systemd/fundetec-production-receipts-scan.service.template'
        self.texto = ruta.read_text(encoding='utf-8')
        self.unit = configparser.ConfigParser(interpolation=None)
        self.unit.read_string(self.texto)

    def test_identidades_rutas_y_protecciones_exigen_verificacion(self):
        service = self.unit['Service']
        for key, marker in [('User', 'SERVICE_USER'), ('Group', 'SERVICE_GROUP'),
                            ('WorkingDirectory', 'APP_DIR'), ('EnvironmentFile', 'ENV_FILE'),
                            ('ReadWritePaths', 'PRIVATE_ROOT')]:
            self.assertEqual(service[key], f'@@{marker}@@')
        self.assertEqual(service['ExecStart'], '@@VENV_PYTHON@@ manage.py procesar_comprobantes_educativos')
        self.assertEqual(service['Restart'], 'on-failure')
        self.assertEqual(service['RestartSec'], '5')
        self.assertEqual(service['KillSignal'], 'SIGTERM')
        for key in ('NoNewPrivileges', 'PrivateTmp', 'ProtectHome'):
            self.assertEqual(service[key], 'true')
        self.assertEqual(service['ProtectSystem'], 'strict')
        self.assertNotIn('/var/www/fundetec-staging', self.texto)

    def test_condicion_no_importa_django_y_falla_cerrada(self):
        comando = shlex.split(self.unit['Service']['ExecCondition'])
        self.assertEqual(comando[:2], ['@@VENV_PYTHON@@', '-c'])
        self.assertNotIn('django', comando[2])
        for ambiente, flag, esperado in [('production', 'true', 0), ('production', 'false', 1),
                                        ('staging', 'true', 1), ('', '', 1)]:
            with self.subTest(ambiente=ambiente, flag=flag):
                env = {**os.environ, 'DEPLOYMENT_ENVIRONMENT': ambiente,
                       'FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED': flag}
                resultado = subprocess.run([sys.executable, *comando[1:]], env=env,
                                           capture_output=True, timeout=10)
                self.assertEqual(resultado.returncode, esperado, resultado.stderr)
