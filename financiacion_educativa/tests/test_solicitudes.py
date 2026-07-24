from django.core.exceptions import ValidationError
from django.test import TestCase

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.tests.factories import crear_institucion, crear_solicitud


class SolicitudFinanciacionTests(TestCase):
    def test_crea_solicitud_sin_usuario_asociado(self):
        solicitud = crear_solicitud()

        self.assertIsNone(solicitud.usuario)
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
        )
        self.assertEqual(solicitud.historial_estados.count(), 1)

    def test_referencia_externa_es_unica_por_institucion(self):
        institucion = crear_institucion()
        crear_solicitud(institucion=institucion, referencia='MISMA-REF')

        with self.assertRaises(ValidationError):
            crear_solicitud(institucion=institucion, referencia='MISMA-REF')

    def test_misma_referencia_se_permite_en_instituciones_distintas(self):
        solicitud_a = crear_solicitud(
            institucion=crear_institucion('1'),
            referencia='COMPARTIDA',
        )
        solicitud_b = crear_solicitud(
            institucion=crear_institucion('2'),
            referencia='COMPARTIDA',
        )

        self.assertNotEqual(solicitud_a.id, solicitud_b.id)

    def test_modelo_no_tiene_campos_de_desembolso_o_transferencia(self):
        campos = {field.name for field in SolicitudFinanciacionEducativa._meta.fields}

        self.assertFalse({
            'desembolso',
            'fecha_desembolso',
            'monto_desembolsado',
            'transferencia',
            'cuenta_destino',
        }.intersection(campos))
