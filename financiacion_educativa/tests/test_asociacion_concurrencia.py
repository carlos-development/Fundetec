from concurrent.futures import ThreadPoolExecutor
import threading

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
        correo_solicitud = 'asociacion-concurrente@example.test'
        solicitud = crear_solicitud(
            referencia='ASOCIACION-CONCURRENTE',
            correo=correo_solicitud,
        )
        emitida = emitir_invitacion_continuacion(solicitud=solicitud)
        User = get_user_model()
        usuarios = [
            User.objects.create_user(
                username=f'concurrente-{indice}@example.com',
                email=correo_solicitud,
                password='ClaveConcurrente-2026',
            )
            for indice in range(2)
        ]
        barrera = threading.Barrier(2)
        errores = []

        def asociar(usuario_id):
            close_old_connections()
            try:
                usuario = User.objects.get(pk=usuario_id)
                barrera.wait(timeout=5)
                try:
                    resultado = asociar_usuario_mediante_invitacion(
                        invitacion_id=emitida.invitacion.pk,
                        usuario=usuario,
                    )
                    return ('ok', resultado.solicitud.usuario_id)
                except InvitacionNoValida:
                    return ('rechazada', usuario_id)
            except Exception as error:  # pragma: no cover - diagnostico de hilo
                errores.append(error)
                return ('error', usuario_id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(asociar, [usuario.pk for usuario in usuarios]))

        self.assertEqual(errores, [])
        self.assertEqual([estado for estado, _ in resultados].count('ok'), 1)
        self.assertEqual(
            [estado for estado, _ in resultados].count('rechazada'),
            1,
        )
        solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
        self.assertIn(solicitud.usuario_id, {usuario.pk for usuario in usuarios})
