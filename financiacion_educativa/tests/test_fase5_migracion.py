from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PreservacionFotografiasLegadasMigrationTests(TransactionTestCase):
    migrate_from = ('financiacion_educativa', '0004_eventoparticipantefinanciacion_evidenciamatricula_and_more')
    migrate_to = ('financiacion_educativa', '0005_cuotaamortizacioneducativa_and_more')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        apps = executor.loader.project_state([self.migrate_from]).apps
        Institucion = apps.get_model('instituciones', 'Institucion')
        Solicitud = apps.get_model(
            'financiacion_educativa',
            'SolicitudFinanciacionEducativa',
        )
        Condiciones = apps.get_model(
            'financiacion_educativa',
            'CondicionesFinancieras',
        )
        institucion = Institucion.objects.create(
            nombre_comercial='Institucion legado',
            razon_social='Institucion legado SAS',
            numero_identificacion_tributaria='900777001',
        )
        solicitud = Solicitud.objects.create(
            institucion=institucion,
            referencia_externa='LEGADO-001',
            nombres='Persona',
            apellidos='Legada',
            celular='3001234567',
            correo='legado@example.com',
            direccion='Direccion',
            valor_plan=Decimal('1000000.00'),
            plazo_meses=3,
            nombre_curso='Curso',
        )
        self.fotografia_id = Condiciones.objects.create(
            solicitud=solicitud,
            valor_financiado=Decimal('1000000.25'),
            plazo_meses=3,
            tasa_interes_mensual=Decimal('1.9000'),
            tasa_comision=Decimal('10.0000'),
            valor_comision=Decimal('100000.03'),
            tasa_iva_comision=Decimal('19.0000'),
            valor_iva_comision=Decimal('19000.01'),
            capital_financiado=Decimal('1119000.29'),
            valor_cuota_estimada=Decimal('380000.17'),
            interes_total_estimado=Decimal('21000.22'),
            total_estimado=Decimal('1140000.51'),
            metodo_calculo='FRENCH_AMORTIZATION',
            base_calculo={'origen': 'legado'},
            version_regla='caracterizacion-v1',
            moneda='COP',
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            [('financiacion_educativa', '0005_cuotaamortizacioneducativa_and_more')]
        )
        super().tearDown()

    def test_preserva_importes_y_marca_registro_como_legado_inactivo(self):
        Condiciones = self.apps.get_model(
            'financiacion_educativa',
            'CondicionesFinancieras',
        )
        fotografia = Condiciones.objects.get(pk=self.fotografia_id)

        self.assertEqual(fotografia.valor_financiado, Decimal('1000000.25'))
        self.assertEqual(fotografia.valor_cuota_estimada, Decimal('380000.17'))
        self.assertEqual(fotografia.version_regla, 'caracterizacion-v1')
        self.assertTrue(fotografia.es_legado)
        self.assertTrue(fotografia.bloqueada)
        self.assertFalse(fotografia.activa)
        self.assertIsNone(fotografia.configuracion_id)
        self.assertEqual(fotografia.numero_version, 1)
