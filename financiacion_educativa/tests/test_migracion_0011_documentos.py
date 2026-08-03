from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import migrations
from django.test import TestCase

from financiacion_educativa.models import EvidenciaMatricula
from financiacion_educativa.tests.factories import crear_solicitud


migracion_0011 = import_module(
    'financiacion_educativa.migrations.'
    '0011_alter_documentofinanciacion_options_and_more'
)
impedir_reversion_soportes_nulos = (
    migracion_0011.impedir_reversion_soportes_nulos
)


class ReversionMigracionDocumentalTests(TestCase):
    def test_precondicion_es_lo_primero_que_se_ejecuta_al_revertir(self):
        self.assertIsInstance(migracion_0011.Migration.operations[-1], migrations.RunPython)

    def test_reversion_se_detiene_sin_alterar_evidencia_sin_soporte(self):
        usuario = get_user_model().objects.create_user(
            username='migration-docs@example.com',
            email='migration-docs@example.com',
            password='Clave-2026',
        )
        solicitud = crear_solicitud(usuario=usuario, referencia='MIG-0011')
        evidencia = EvidenciaMatricula.objects.create(
            solicitud=solicitud,
            institucion_declarada='Institucion aliada',
            programa_curso='Programa educativo',
            periodo_academico='2026-2',
            registrado_por=usuario,
        )

        with self.assertRaisesMessage(
            RuntimeError,
            'No puede revertirse 0011',
        ):
            impedir_reversion_soportes_nulos(apps, None)

        evidencia.refresh_from_db()
        self.assertIsNone(evidencia.documento_soporte_id)

    def test_reversion_no_se_bloquea_sin_soportes_nulos(self):
        impedir_reversion_soportes_nulos(apps, None)
