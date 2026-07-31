from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from financiacion_educativa.choices import (
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    ParticipanteFinanciacion,
)
from financiacion_educativa.services.asociacion import (
    asociar_usuario_mediante_invitacion,
)
from financiacion_educativa.services.invitaciones import (
    InvitacionNoValida,
    emitir_invitacion_continuacion,
    obtener_invitacion_vigente_por_token,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(BRAND_PUBLIC_BASE_URL='https://credito.example.com')
class AsociacionUsuarioTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='usuario@example.com',
            email='usuario@example.com',
            password='UnaClaveSegura-2026',
        )
        self.otro_usuario = User.objects.create_user(
            username='otro@example.com',
            email='otro@example.com',
            password='OtraClaveSegura-2026',
        )
        self.solicitud = crear_solicitud(correo=self.usuario.email)
        self.emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)

    def test_asocia_usuario_consume_token_transiciona_y_audita(self):
        resultado = asociar_usuario_mediante_invitacion(
            invitacion_id=self.emitida.invitacion.pk,
            usuario=self.usuario,
        )
        self.emitida.invitacion.refresh_from_db()

        self.assertEqual(resultado.solicitud.usuario, self.usuario)
        self.assertEqual(
            resultado.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_TERMS,
        )
        self.assertEqual(
            self.emitida.invitacion.estado,
            EstadoInvitacionContinuacion.CONSUMED,
        )
        self.assertEqual(self.emitida.invitacion.consumida_por, self.usuario)
        historial = resultado.solicitud.historial_estados.get(
            estado_nuevo=EstadoSolicitudFinanciacion.PENDING_TERMS
        )
        self.assertEqual(historial.actor, self.usuario)
        self.assertEqual(historial.metadata['event'], 'USER_ASSOCIATED')
        self.assertIsNone(
            obtener_invitacion_vigente_por_token(self.emitida.token)
        )

    def test_asociacion_repetida_mismo_usuario_es_idempotente(self):
        primera = asociar_usuario_mediante_invitacion(
            invitacion_id=self.emitida.invitacion.pk,
            usuario=self.usuario,
        )
        segunda = asociar_usuario_mediante_invitacion(
            invitacion_id=self.emitida.invitacion.pk,
            usuario=self.usuario,
        )

        self.assertFalse(primera.repetida)
        self.assertTrue(segunda.repetida)
        self.assertEqual(
            self.solicitud.historial_estados.filter(
                estado_nuevo=EstadoSolicitudFinanciacion.PENDING_TERMS
            ).count(),
            1,
        )

    def test_asociacion_consumida_rechaza_cuenta_con_correo_modificado(self):
        asociar_usuario_mediante_invitacion(
            invitacion_id=self.emitida.invitacion.pk,
            usuario=self.usuario,
        )
        self.usuario.email = 'correo-cambiado@example.com'
        self.usuario.save(update_fields=['email'])

        with self.assertRaises(InvitacionNoValida):
            asociar_usuario_mediante_invitacion(
                invitacion_id=self.emitida.invitacion.pk,
                usuario=self.usuario,
            )

    def test_otra_cuenta_no_puede_apropiarse_de_solicitud(self):
        asociar_usuario_mediante_invitacion(
            invitacion_id=self.emitida.invitacion.pk,
            usuario=self.usuario,
        )

        with self.assertRaises(InvitacionNoValida):
            asociar_usuario_mediante_invitacion(
                invitacion_id=self.emitida.invitacion.pk,
                usuario=self.otro_usuario,
            )

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.usuario, self.usuario)

    def test_no_crea_participantes_ni_documentos(self):
        asociar_usuario_mediante_invitacion(
            invitacion_id=self.emitida.invitacion.pk,
            usuario=self.usuario,
        )

        self.assertFalse(ParticipanteFinanciacion.objects.exists())
        self.assertFalse(DocumentoFinanciacion.objects.exists())

    def test_estado_no_elegible_no_consume_invitacion_emitida(self):
        self.solicitud.estado = EstadoSolicitudFinanciacion.CANCELLED
        self.solicitud.save(update_fields=['estado'])

        with self.assertRaises(InvitacionNoValida):
            asociar_usuario_mediante_invitacion(
                invitacion_id=self.emitida.invitacion.pk,
                usuario=self.usuario,
            )

        self.emitida.invitacion.refresh_from_db()
        self.assertEqual(
            self.emitida.invitacion.estado,
            EstadoInvitacionContinuacion.ACTIVE,
        )
