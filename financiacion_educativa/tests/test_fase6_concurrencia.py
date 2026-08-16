from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from financiacion_educativa.choices import OrigenEntregaInvitacion
from financiacion_educativa.models import EntregaInvitacionContinuacion
from financiacion_educativa.services.orquestacion import (
    programar_invitacion_inicial,
)
from financiacion_educativa.services.outbox_correos import procesar_siguiente_correo
from financiacion_educativa.tests.delivery_backends import (
    RecordingInvitationDeliveryBackend,
)
from financiacion_educativa.tests.factories import crear_solicitud


BACKEND_EXITO = (
    'financiacion_educativa.tests.delivery_backends.'
    'RecordingInvitationDeliveryBackend'
)


@override_settings(
    BRAND_PUBLIC_BASE_URL='https://credito.example.com',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_EXITO,
)
class ConcurrenciaInvitacionFase6Tests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        RecordingInvitationDeliveryBackend.reset()
        self.solicitud = crear_solicitud(
            referencia='FASE6-CONCURRENCIA',
            correo='fase6-concurrencia@example.test',
        )

    def _programar(self, barrera, errores):
        close_old_connections()
        try:
            barrera.wait(timeout=5)
            return programar_invitacion_inicial(
                solicitud=self.solicitud
            ).creada
        except Exception as error:  # pragma: no cover - diagnostico de hilo
            errores.append(error)
            return None
        finally:
            close_old_connections()

    @skipUnless(
        connection.vendor == 'postgresql',
        'La concurrencia con bloqueos de fila se valida en staging PostgreSQL.',
    )
    def test_dos_emisiones_concurrentes_crean_una_entrega_inicial(self):
        barrera = threading.Barrier(2)
        errores = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = [
                executor.submit(self._programar, barrera, errores),
                executor.submit(self._programar, barrera, errores),
            ]
            creadas = [resultado.result() for resultado in resultados]

        self.assertEqual(errores, [])
        self.assertCountEqual(creadas, [True, False])
        self.assertEqual(
            EntregaInvitacionContinuacion.objects.filter(
                origen=OrigenEntregaInvitacion.INITIAL,
            ).count(),
            1,
        )
        self.assertEqual(
            EntregaInvitacionContinuacion.objects.filter(
                correo_outbox__isnull=False,
            ).count(),
            1,
        )
        self.assertFalse(RecordingInvitationDeliveryBackend.deliveries)

        procesar_siguiente_correo()

        self.assertEqual(len(RecordingInvitationDeliveryBackend.deliveries), 1)
