from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from financiacion_educativa.choices import OrigenEntregaInvitacion
from financiacion_educativa.models import EntregaInvitacionContinuacion
from financiacion_educativa.services.orquestacion import (
    programar_invitacion_inicial,
)
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
        self.solicitud = crear_solicitud(referencia='FASE6-CONCURRENCIA')

    def _programar(self):
        close_old_connections()
        try:
            programar_invitacion_inicial(solicitud=self.solicitud)
        finally:
            close_old_connections()

    @skipUnless(
        connection.vendor == 'postgresql',
        'La concurrencia con bloqueos de fila se valida en staging PostgreSQL.',
    )
    def test_dos_emisiones_concurrentes_crean_una_entrega_inicial(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = [
                executor.submit(self._programar),
                executor.submit(self._programar),
            ]
            for resultado in resultados:
                resultado.result()

        self.assertEqual(
            EntregaInvitacionContinuacion.objects.filter(
                origen=OrigenEntregaInvitacion.INITIAL,
            ).count(),
            1,
        )
        self.assertEqual(len(RecordingInvitationDeliveryBackend.deliveries), 1)
