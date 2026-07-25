from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import InvitacionContinuacionSolicitud
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
    obtener_invitacion_vigente_por_token,
    revocar_invitacion_continuacion,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(
    BRAND_PUBLIC_BASE_URL='https://credito.example.com',
    FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS=48,
)
class InvitacionesContinuacionTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud()

    def test_token_tiene_entropia_y_no_se_persiste_en_texto_plano(self):
        emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)
        otra_solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='REF-OTRA',
        )
        otra = emitir_invitacion_continuacion(solicitud=otra_solicitud)

        self.assertGreaterEqual(len(emitida.token), 64)
        self.assertNotEqual(emitida.token, otra.token)
        self.assertNotEqual(emitida.token, emitida.invitacion.token_hash)
        self.assertEqual(len(emitida.invitacion.token_hash), 64)
        campos = {
            campo.name for campo in InvitacionContinuacionSolicitud._meta.fields
        }
        self.assertNotIn('token', campos)
        self.assertFalse(hasattr(emitida.invitacion, 'token'))
        self.assertNotIn(emitida.token, repr(emitida))

    def test_url_se_construye_desde_configuracion_y_reverse(self):
        emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)

        self.assertEqual(
            emitida.url,
            (
                'https://credito.example.com/financiacion-educativa/'
                f'continuar/{emitida.token}/'
            ),
        )

    def test_duracion_proviene_de_configuracion(self):
        antes = timezone.now()
        emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)

        duracion = emitida.invitacion.vence_en - antes
        self.assertGreaterEqual(duracion, timedelta(hours=48))
        self.assertLess(duracion, timedelta(hours=48, seconds=2))

    def test_nueva_emision_revoca_anterior_y_audita_eventos(self):
        primera = emitir_invitacion_continuacion(solicitud=self.solicitud)
        segunda = emitir_invitacion_continuacion(solicitud=self.solicitud)
        primera.invitacion.refresh_from_db()

        self.assertEqual(
            primera.invitacion.estado,
            EstadoInvitacionContinuacion.REVOKED,
        )
        self.assertEqual(
            segunda.invitacion.estado,
            EstadoInvitacionContinuacion.ACTIVE,
        )
        self.assertFalse(
            obtener_invitacion_vigente_por_token(primera.token)
        )
        self.assertEqual(
            set(primera.invitacion.eventos.values_list('tipo', flat=True)),
            {TipoEventoInvitacion.ISSUED, TipoEventoInvitacion.REVOKED},
        )

    def test_vencida_revocada_alterada_y_uuid_no_son_validos(self):
        emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)

        self.assertIsNone(
            obtener_invitacion_vigente_por_token(f'{emitida.token}alterado')
        )
        self.assertIsNone(
            obtener_invitacion_vigente_por_token(str(self.solicitud.pk))
        )

        InvitacionContinuacionSolicitud.objects.filter(
            pk=emitida.invitacion.pk
        ).update(vence_en=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(
            obtener_invitacion_vigente_por_token(emitida.token)
        )

        emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)
        revocar_invitacion_continuacion(invitacion=emitida.invitacion)
        self.assertIsNone(
            obtener_invitacion_vigente_por_token(emitida.token)
        )

    def test_solicitud_en_estado_no_elegible_no_emite(self):
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_TERMS
        self.solicitud.save(update_fields=['estado'])

        with self.assertRaises(ValidationError):
            emitir_invitacion_continuacion(solicitud=self.solicitud)

    def test_token_no_aparece_en_logs_ni_configuracion_admin(self):
        with self.assertNoLogs('financiacion_educativa', level='INFO'):
            emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)

        modelo_admin = admin.site._registry[InvitacionContinuacionSolicitud]
        campos_lectura = modelo_admin.get_readonly_fields(None, emitida.invitacion)
        self.assertIn('token_hash', campos_lectura)
        self.assertNotIn(emitida.token, str(campos_lectura))
