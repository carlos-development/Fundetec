import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command, CommandError
from django.test import TestCase

from financiacion_educativa.choices import EstadoVersionTerminos
from financiacion_educativa.models import VersionTerminosFinanciacion


class CargarBorradoresTerminosTests(TestCase):
    def setUp(self):
        self.directorio = TemporaryDirectory()
        self.addCleanup(self.directorio.cleanup)
        self.archivo = Path(self.directorio.name) / 'terminos.json'
        self.registro = {
            'tipo': 'TERMS', 'version': 'terminos-v2', 'titulo': 'Terminos',
            'contenido': 'Texto completo suministrado para revision.', 'obligatorio': True,
        }
        self.escribir([self.registro])

    def escribir(self, registros):
        self.archivo.write_text(json.dumps(registros), encoding='utf-8')

    def cargar(self, **kwargs):
        call_command('cargar_borradores_terminos', file=self.archivo, stdout=StringIO(), **kwargs)

    def test_inspeccion_sin_mutaciones_y_carga_idempotente(self):
        self.cargar()
        self.assertFalse(VersionTerminosFinanciacion.objects.exists())
        self.cargar(confirm=True)
        version = VersionTerminosFinanciacion.objects.get()
        self.assertEqual(version.estado, EstadoVersionTerminos.DRAFT)
        self.assertIsNone(version.publicada_en)
        self.assertEqual(version.contenido, self.registro['contenido'])
        self.cargar(confirm=True)
        self.assertEqual(VersionTerminosFinanciacion.objects.count(), 1)

    def test_conflicto_no_sobrescribe_y_revierte_lote(self):
        self.cargar(confirm=True)
        self.escribir([
            {**self.registro, 'version': 'nueva'},
            {**self.registro, 'contenido': 'Otro texto distinto'},
        ])
        with self.assertRaises(CommandError):
            self.cargar(confirm=True)
        self.assertEqual(VersionTerminosFinanciacion.objects.count(), 1)
        self.assertEqual(VersionTerminosFinanciacion.objects.get().contenido, self.registro['contenido'])

    def test_no_admite_instrucciones_de_publicacion_en_json(self):
        self.escribir([{**self.registro, 'estado': 'PUBLISHED'}])
        with self.assertRaises(CommandError):
            self.cargar(confirm=True)
        self.assertFalse(VersionTerminosFinanciacion.objects.exists())
