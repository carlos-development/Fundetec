from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.asociacion import (
    asociar_usuario_mediante_invitacion,
)
from financiacion_educativa.services.invitaciones import (
    InvitacionNoValida,
    emitir_invitacion_continuacion,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(BRAND_PUBLIC_BASE_URL='https://credito.example.com')
class AsociacionConcurrenteTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_consumo_concurrente_solo_asocia_una_cuenta(self):
        solicitud = crear_solicitud()
        emitida = emitir_invitacion_continuacion(solicitud=solicitud)
        User = get_user_model()
        usuarios = [
            User.objects.create_user(
                username=f'concurrente-{indice}@example.com',
                email=f'concurrente-{indice}@example.com',
                password='ClaveConcurrente-2026',
            )
            for indice in range(2)
        ]

        def asociar(usuario_id):
            close_old_connections()
            usuario = User.objects.get(pk=usuario_id)
            try:
                resultado = asociar_usuario_mediante_invitacion(
                    invitacion_id=emitida.invitacion.pk,
                    usuario=usuario,
                )
                valor = ('ok', resultado.solicitud.usuario_id)
            except InvitacionNoValida:
                valor = ('rechazada', usuario_id)
            close_old_connections()
            return valor

        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(asociar, [usuario.pk for usuario in usuarios]))

        self.assertEqual([estado for estado, _ in resultados].count('ok'), 1)
        self.assertEqual(
            [estado for estado, _ in resultados].count('rechazada'),
            1,
        )
        solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
        self.assertIn(solicitud.usuario_id, {usuario.pk for usuario in usuarios})
