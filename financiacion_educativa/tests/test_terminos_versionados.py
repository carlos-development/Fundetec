from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    EstadoVersionTerminos,
    TipoConsentimiento,
)
from financiacion_educativa.models import Consentimiento, VersionTerminosFinanciacion
from financiacion_educativa.services.consentimientos import (
    calcular_evidencia_consentimiento,
)
from financiacion_educativa.services.terminos import (
    aceptar_terminos_solicitud,
    obtener_versiones_terminos_vigentes,
    publicar_version_terminos,
    retirar_version_terminos,
)
from financiacion_educativa.tests.factories import crear_solicitud


CONTENIDO_FIXTURE = 'FIXTURE DE PRUEBA SIN VALIDEZ LEGAL.'


class TerminosVersionadosTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='terminos@example.com',
            email='terminos@example.com',
            password='ClaveSegura-2026',
        )
        self.otro = User.objects.create_user(
            username='otro-terminos@example.com',
            email='otro-terminos@example.com',
            password='OtraClave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_TERMS
        self.solicitud.save(update_fields=['estado'])

    def _borrador(self, version='fixture-v1', tipo=TipoConsentimiento.TERMS):
        return VersionTerminosFinanciacion.objects.create(
            tipo=tipo,
            version=version,
            titulo='Terminos fixture',
            contenido=CONTENIDO_FIXTURE,
            obligatorio=True,
        )

    def test_crea_borrador_con_hash_exacto(self):
        version = self._borrador()

        self.assertEqual(version.estado, EstadoVersionTerminos.DRAFT)
        self.assertEqual(
            version.hash_integridad,
            VersionTerminosFinanciacion.calcular_hash(CONTENIDO_FIXTURE),
        )

    def test_solo_publicada_y_vigente_se_presenta(self):
        vigente = publicar_version_terminos(version=self._borrador())
        self._borrador(version='fixture-futuro')
        futura = self._borrador(
            version='fixture-v2',
            tipo=TipoConsentimiento.DATA_PROCESSING,
        )
        publicar_version_terminos(
            version=futura,
            vigente_desde=timezone.now() + timedelta(days=1),
        )

        actuales = obtener_versiones_terminos_vigentes(obligatorios=True)

        self.assertEqual(actuales, [vigente])

    def test_publicada_no_se_modifica_directamente(self):
        version = publicar_version_terminos(version=self._borrador())
        version.contenido = 'Contenido manipulado'

        with self.assertRaises(ValidationError):
            version.save()

    def test_retirada_y_futura_no_pueden_aceptarse(self):
        retirada = publicar_version_terminos(version=self._borrador())
        retirar_version_terminos(version=retirada)

        with self.assertRaises(ValidationError):
            aceptar_terminos_solicitud(
                solicitud=self.solicitud,
                usuario=self.usuario,
                versiones=[retirada],
            )

        futura = publicar_version_terminos(
            version=self._borrador(version='fixture-v2'),
            vigente_desde=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            aceptar_terminos_solicitud(
                solicitud=self.solicitud,
                usuario=self.usuario,
                versiones=[futura],
            )

    def test_no_avanza_si_falta_un_termino_obligatorio(self):
        primera = publicar_version_terminos(version=self._borrador())
        publicar_version_terminos(
            version=self._borrador(
                version='fixture-datos-v1',
                tipo=TipoConsentimiento.DATA_PROCESSING,
            )
        )

        with self.assertRaises(ValidationError):
            aceptar_terminos_solicitud(
                solicitud=self.solicitud,
                usuario=self.usuario,
                versiones=[primera],
            )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_TERMS,
        )

    def test_aceptacion_guarda_evidencia_y_avanza_con_historial(self):
        version = publicar_version_terminos(version=self._borrador())

        resultado = aceptar_terminos_solicitud(
            solicitud=self.solicitud,
            usuario=self.usuario,
            versiones=[version],
            ip_address='127.0.0.1',
            user_agent='Navegador fixture',
        )
        consentimiento = resultado.consentimientos[0]

        self.assertEqual(consentimiento.version_texto, version.version)
        self.assertEqual(consentimiento.usuario, self.usuario)
        self.assertEqual(
            consentimiento.evidencia_hash,
            calcular_evidencia_consentimiento(
                tipo=version.tipo,
                version_texto=version.version,
                texto=CONTENIDO_FIXTURE,
            ),
        )
        self.assertEqual(consentimiento.ip_address, '127.0.0.1')
        self.assertEqual(
            resultado.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        )
        self.assertTrue(
            resultado.solicitud.historial_estados.filter(
                estado_nuevo=EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
                actor=self.usuario,
            ).exists()
        )

    def test_reintento_no_duplica_y_consentimiento_es_inmutable(self):
        version = publicar_version_terminos(version=self._borrador())
        aceptar_terminos_solicitud(
            solicitud=self.solicitud,
            usuario=self.usuario,
            versiones=[version],
        )

        repetida = aceptar_terminos_solicitud(
            solicitud=self.solicitud,
            usuario=self.usuario,
            versiones=[version],
        )

        self.assertTrue(repetida.repetida)
        self.assertEqual(Consentimiento.objects.count(), 1)
        consentimiento = Consentimiento.objects.get()
        consentimiento.version_texto = 'manipulada'
        with self.assertRaises(ValidationError):
            consentimiento.save()

    def test_otro_usuario_no_acepta(self):
        version = publicar_version_terminos(version=self._borrador())

        with self.assertRaises(ValidationError):
            aceptar_terminos_solicitud(
                solicitud=self.solicitud,
                usuario=self.otro,
                versiones=[version],
            )

        self.assertFalse(Consentimiento.objects.exists())
