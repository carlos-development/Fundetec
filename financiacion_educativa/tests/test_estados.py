from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from financiacion_educativa.choices import EstadoSolicitudFinanciacion as Estado
from financiacion_educativa.models import HistorialEstadoSolicitud
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.tests.factories import crear_solicitud


class TransicionesEstadoTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud()

    def test_transicion_valida_actualiza_y_registra_historial(self):
        actualizada = transicionar_solicitud(
            solicitud=self.solicitud,
            nuevo_estado=Estado.PENDING_TERMS,
            motivo='Usuario vinculado.',
        )

        self.assertEqual(actualizada.estado, Estado.PENDING_TERMS)
        self.assertEqual(actualizada.historial_estados.count(), 2)
        ultimo = actualizada.historial_estados.get(
            estado_nuevo=Estado.PENDING_TERMS
        )
        self.assertEqual(ultimo.estado_anterior, Estado.PENDING_USER_REGISTRATION)
        self.assertEqual(ultimo.estado_nuevo, Estado.PENDING_TERMS)

    def test_transicion_invalida_se_rechaza(self):
        with self.assertRaises(ValidationError):
            transicionar_solicitud(
                solicitud=self.solicitud,
                nuevo_estado=Estado.ACTIVE,
            )

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, Estado.PENDING_USER_REGISTRATION)
        self.assertEqual(self.solicitud.historial_estados.count(), 1)

    def test_cambio_de_estado_es_atomico_si_falla_historial(self):
        with patch.object(
            HistorialEstadoSolicitud.objects,
            'create',
            side_effect=RuntimeError('fallo controlado'),
        ):
            with self.assertRaises(RuntimeError):
                transicionar_solicitud(
                    solicitud=self.solicitud,
                    nuevo_estado=Estado.PENDING_TERMS,
                )

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, Estado.PENDING_USER_REGISTRATION)
        self.assertEqual(self.solicitud.historial_estados.count(), 1)
