from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from unittest import skipUnless
from urllib.request import urlopen
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from financiacion_educativa.models import ArtefactoContractualEducativo, OutboxCorreoEducativo
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services.libro_pagos import resumen_ingresos
from financiacion_educativa.tests.restauracion_helpers import (
    docker, inspeccionar, manifiesto, respaldar_privados, restaurar_privados, validar_contenedores,
)
from financiacion_educativa.tests.restauracion_probe import firma_calendario, publico_resumido
from financiacion_educativa.tests.test_aplicacion_cuotas import DatosAplicacion
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO, pdf_sintetico


class GuardasRestauracionTests(SimpleTestCase):
    def datos(self, identificador='a', puerto='55455'):
        return {'Id': identificador, 'Config': {'Labels': {'fundetec.purpose': 'restore-test'}},
            'State': {'Running': True}, 'Mounts': [{'Type': 'tmpfs'}],
            'NetworkSettings': {'Ports': {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': puerto}]}}}

    def test_acepta_solo_dos_contenedores_locales_y_base_del_runner(self):
        self.assertEqual(validar_contenedores(self.datos(), self.datos('b', '55456'),
            {'HOST': '127.0.0.1', 'PORT': '55455', 'NAME': 'test_restore_source'}), 55456)

    def test_rechaza_base_compartida_host_remoto_y_mismo_contenedor(self):
        origen, destino = self.datos(), self.datos('b', '55456')
        for cambios in ({'NAME': 'staging'}, {'HOST': 'servidor'}, {'PORT': '5432'}):
            with self.subTest(cambios=cambios), self.assertRaises(ValueError):
                validar_contenedores(origen, destino, {**dict(HOST='127.0.0.1', PORT='55455', NAME='test_base'), **cambios})
        with self.assertRaises(ValueError):
            validar_contenedores(origen, origen, dict(HOST='127.0.0.1', PORT='55455', NAME='test_base'))

    def test_rechaza_docker_no_etiquetado_bind_y_puerto_publico(self):
        for transformar in (lambda d: d['Config'].update(Labels={}),
                lambda d: d.update(Mounts=[{'Type': 'bind'}]),
                lambda d: d['NetworkSettings']['Ports']['5432/tcp'][0].update(HostIp='0.0.0.0')):
            destino = deepcopy(self.datos('b', '55456'))
            transformar(destino)
            with self.assertRaises(ValueError):
                validar_contenedores(self.datos(), destino, dict(HOST='127.0.0.1', PORT='55455', NAME='test_base'))

    def test_archivo_privado_rechaza_traversal_y_destino_existente(self):
        import io
        import tarfile
        with TemporaryDirectory() as directorio:
            raiz = Path(directorio)
            archivo = raiz / 'inseguro.tar.gz'
            with tarfile.open(archivo, 'w:gz') as salida:
                entrada = tarfile.TarInfo('../../fuera')
                entrada.size = 1
                salida.addfile(entrada, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                restaurar_privados(archivo, raiz / 'destino')
            with self.assertRaises(ValueError):
                restaurar_privados(archivo, raiz)
            self.assertFalse((raiz / 'destino').exists())


@skipUnless(os.environ.get('EDU_RESTORE_ALLOW_ISOLATED') == 'true', 'Requiere dos PostgreSQL Docker aislados autorizados.')
@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=False,
    FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False)
class RestauracionOperativaTests(DatosAplicacion, TransactionTestCase):
    def test_pg_dump_restore_privados_arranque_contrato_y_cuadre(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.assertEqual(settings.DEPLOYMENT_ENVIRONMENT, 'test')
        origen = inspeccionar(os.environ['EDU_RESTORE_DOCKER_SOURCE'])
        destino = inspeccionar(os.environ['EDU_RESTORE_DOCKER_TARGET'])
        puerto = validar_contenedores(origen, destino, connection.settings_dict)
        usuario = connection.settings_dict['USER']
        nombre = 'restore_' + uuid4().hex
        reporte = self.ingreso()
        self.aplicar(reporte)
        # A second bank-confirmed income remains unallocated, not a paid instalment.
        self.ingreso(monto='50000', referencia='synthetic-second-report')
        artefacto = ArtefactoContractualEducativo.objects.get(solicitud=self.solicitud, tipo='PROMISSORY_NOTE')
        for campo, huella, tamano in [('archivo', 'hash_sha256', 'tamano_bytes'),
                ('archivo_firmado', 'hash_firmado_sha256', 'tamano_firmado_bytes')]:
            contenido = pdf_sintetico().read()
            recurso = getattr(artefacto, campo)
            recurso.name = recurso.storage.save(recurso.name, ContentFile(contenido))
            setattr(artefacto, huella, hashlib.sha256(contenido).hexdigest())
            setattr(artefacto, tamano, len(contenido))
        artefacto.save()
        esperado = {'publico': publico_resumido(self.solicitud),
            'outbox_count': OutboxCorreoEducativo.objects.count(),
            'calendario': firma_calendario(self.solicitud), 'comprobante': reporte.comprobante.sha256,
            'totales': {k: str(v) for k, v in resumen_ingresos(ConciliacionRecaudoEducativo.objects.all()).items()}}
        with TemporaryDirectory(prefix='fundetec-restore-') as temporal:
            iniciado = time.monotonic()
            raiz = Path(temporal)
            privados = raiz / 'private.tar.gz'
            manifest = respaldar_privados(self.temporal.name, privados)
            dump = raiz / 'database.dump'
            ruta_dump = '/tmp/' + nombre + '.dump'
            creada = False
            servidor = None
            try:
                docker('exec', origen['Id'], 'pg_dump', '-U', usuario, '--format=custom', '--file', ruta_dump,
                    '--dbname', connection.settings_dict['NAME'])
                docker('cp', origen['Id'] + ':' + ruta_dump, str(dump))
                self.assertGreater(dump.stat().st_size, 0)
                restaurado = restaurar_privados(privados, raiz / 'restored')
                self.assertEqual(manifiesto(restaurado), manifest)
                docker('cp', str(dump), destino['Id'] + ':' + ruta_dump)
                docker('exec', destino['Id'], 'createdb', '-U', usuario, nombre)
                creada = True
                docker('exec', destino['Id'], 'pg_restore', '-U', usuario, '--exit-on-error',
                    '--no-owner', '--no-privileges', '--dbname', nombre, ruta_dump)
                entorno = {k: v for k, v in os.environ.items() if k in (
                    'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'HOME')}
                entorno.update(DJANGO_LOAD_DOTENV='false', DEBUG='true', DEPLOYMENT_ENVIRONMENT='test',
                    SECRET_KEY='synthetic-restore-only-not-a-production-secret',
                    ALLOWED_HOSTS='127.0.0.1,localhost,testserver',
                    DATABASE_URL=f"postgresql://{usuario}:{connection.settings_dict['PASSWORD']}@127.0.0.1:{puerto}/{nombre}",
                    FINANCIACION_EDUCATIVA_PRIVATE_ROOT=str(restaurado),
                    FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED='true',
                    FINANCIACION_EDUCATIVA_LIBRO_ENABLED='false',
                    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED='false',
                    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED='false',
                    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED='false',
                    FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED='false',
                    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_LIVE_DELIVERY_ENABLED='false',
                    EDU_RESTORE_PROBE='synthetic-only', EDU_RESTORE_EXPECTED=json.dumps(esperado, default=str),
                    EDU_RESTORE_APPLICATION=str(self.solicitud.pk), EDU_RESTORE_REPORT=str(reporte.pk))
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    puerto_http = sock.getsockname()[1]
                comando = [sys.executable, 'manage.py']
                servidor = subprocess.Popen([*comando, 'runserver', f'127.0.0.1:{puerto_http}', '--noreload'],
                    env=entorno, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                limite = time.monotonic() + 30
                while True:
                    try:
                        with urlopen(f'http://127.0.0.1:{puerto_http}/health/', timeout=2) as respuesta:
                            self.assertEqual(respuesta.status, 200)
                            break
                    except OSError:
                        if servidor.poll() is not None or time.monotonic() >= limite:
                            self.fail('La aplicacion restaurada no arranco en localhost.')
                        time.sleep(0.2)
                resultado = subprocess.run([*comando, 'shell', '-c',
                    'from financiacion_educativa.tests.restauracion_probe import ejecutar_probe; ejecutar_probe()'],
                    env=entorno, capture_output=True, text=True, timeout=60)
                fallo_seguro = next((l for l in resultado.stdout.splitlines() if l.startswith('RESTORE_PROBE_FAILURE=')), 'Inicio del probe fallido')
                self.assertEqual(resultado.returncode, 0, fallo_seguro)
                linea = next(l for l in resultado.stdout.splitlines() if l.startswith('RESTORE_PROBE='))
                datos = json.loads(linea.split('=', 1)[1])
                self.assertTrue(datos['privado_legible'])
                self.assertTrue(datos['recaudos_cuadran'])
                print('ENSAYO_RESTAURACION=' + json.dumps({'archivos_restaurados': len(manifest),
                    'database_dump_bytes': dump.stat().st_size, 'health_http': 200,
                    'duracion_segundos': round(time.monotonic() - iniciado, 3), **datos}, sort_keys=True))
            finally:
                if servidor:
                    if servidor.poll() is None:
                        servidor.terminate()
                    try:
                        servidor.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        servidor.kill()
                        servidor.wait(timeout=15)
                if creada:
                    docker('exec', destino['Id'], 'dropdb', '-U', usuario, '--force', nombre)
                for contenedor in (origen, destino):
                    docker('exec', contenedor['Id'], 'rm', '-f', ruta_dump)
