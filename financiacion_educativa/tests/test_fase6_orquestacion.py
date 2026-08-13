from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from io import StringIO
from urllib.parse import urlparse

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from financiacion_educativa.choices import (
    EstadoEntregaInvitacion,
    EstadoInvitacionContinuacion,
    EstadoOutboxCorreoEducativo,
    EstadoSolicitudFinanciacion,
    OrigenEntregaInvitacion,
    TipoConsentimiento,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import (
    EntregaInvitacionContinuacion,
    EventoInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    OutboxCorreoEducativo,
    RegistroIdempotenciaSolicitud,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.entrega_invitaciones import (
    calcular_hmac_destinatario,
)
from financiacion_educativa.services.idempotencia import (
    crear_solicitud_idempotente,
)
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
)
from financiacion_educativa.services.orquestacion import (
    programar_invitacion_inicial,
    reemitir_invitacion_orquestada,
)
from financiacion_educativa.services.outbox_correos import (
    procesar_siguiente_correo,
)
from financiacion_educativa.services.solicitudes import (
    DatosSolicitudFinanciacion,
)
from financiacion_educativa.services.terminos import (
    publicar_version_terminos,
)
from financiacion_educativa.tests.delivery_backends import (
    FailingInvitationDeliveryBackend,
    RecordingInvitationDeliveryBackend,
)
from instituciones.models import Institucion
from instituciones.services.credenciales import crear_credencial_api


BACKEND_EXITO = (
    'financiacion_educativa.tests.delivery_backends.'
    'RecordingInvitationDeliveryBackend'
)
BACKEND_FALLA = (
    'financiacion_educativa.tests.delivery_backends.'
    'FailingInvitationDeliveryBackend'
)
BACKEND_DJANGO = (
    'financiacion_educativa.services.entrega_invitaciones.'
    'DjangoEmailInvitationDeliveryBackend'
)
PAYLOAD = {
    'external_reference': 'FASE6-2026-001',
    'first_names': 'MARIA CAMILA',
    'last_names': 'ROJAS DIAZ',
    'phone': '3001234567',
    'email': 'solicitante@example.com',
    'address': 'Calle 10 # 20-30',
    'plan_value': '2500000.00',
    'term': 6,
    'course_type': 'Tecnologia en sistemas',
}
REGISTRO = {
    'email': 'solicitante@example.com',
    'first_name': 'Maria',
    'last_name': 'Rojas',
    'password1': 'ClaveEducativa-2026',
    'password2': 'ClaveEducativa-2026',
}


@override_settings(
    BRAND_PUBLIC_BASE_URL='https://credito.example.com',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_EXITO,
)
class OrquestacionInvitacionFase6Tests(APITestCase):
    def setUp(self):
        RecordingInvitationDeliveryBackend.reset()
        FailingInvitationDeliveryBackend.reset()
        self.institucion = Institucion.objects.create(
            nombre_comercial='Institucion Fase 6',
            razon_social='Institucion Fase 6 SAS',
            numero_identificacion_tributaria='901600001',
        )
        self.credencial = crear_credencial_api(
            institucion=self.institucion,
            nombre='Pruebas Fase 6',
        )
        self.url = reverse('financiacion_educativa_api:solicitud-crear')

    def _crear(self, *, clave='fase6-idem-001', payload=None, procesar=True):
        respuesta = self.client.post(
            self.url,
            data=payload or deepcopy(PAYLOAD),
            format='json',
            HTTP_AUTHORIZATION=f'ApiKey {self.credencial.token}',
            HTTP_IDEMPOTENCY_KEY=clave,
        )
        if procesar:
            procesar_siguiente_correo()
        return respuesta

    @override_settings(
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_FALLA,
    )
    def test_fallo_del_worker_no_cambia_respuesta_202(self):
        respuesta = self._crear(procesar=False)

        self.assertEqual(respuesta.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            OutboxCorreoEducativo.objects.get().estado,
            EstadoOutboxCorreoEducativo.PENDING,
        )
        procesar_siguiente_correo()

        entrega = EntregaInvitacionContinuacion.objects.get()
        self.assertEqual(entrega.estado, EstadoEntregaInvitacion.FAILED)
        self.assertEqual(
            entrega.codigo_ultimo_error,
            'SMTP_DELIVERY_AMBIGUOUS',
        )
        self.assertEqual(
            OutboxCorreoEducativo.objects.get().estado,
            EstadoOutboxCorreoEducativo.AMBIGUOUS,
        )
        self.assertEqual(len(FailingInvitationDeliveryBackend.deliveries), 1)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_DJANGO,
    )
    def test_adaptador_django_envia_correo_sin_exponer_enlace_en_api(self):
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self._crear()

        self.assertEqual(respuesta.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [PAYLOAD['email']])
        self.assertIn('https://credito.example.com/', mail.outbox[0].body)
        self.assertNotIn('continuation_url', respuesta.data)
        self.assertNotIn('token', respuesta.data)
        self.assertEqual(
            EntregaInvitacionContinuacion.objects.get().estado,
            EstadoEntregaInvitacion.SENT,
        )

    def test_replay_retorna_misma_solicitud_sin_nueva_entrega_ni_correo(self):
        with self.captureOnCommitCallbacks(execute=True):
            primera = self._crear()
        with self.captureOnCommitCallbacks(execute=True):
            segunda = self._crear()

        self.assertEqual(primera.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(segunda.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            primera.data['application_id'],
            segunda.data['application_id'],
        )
        self.assertEqual(segunda['Idempotent-Replayed'], 'true')
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)
        self.assertEqual(InvitacionContinuacionSolicitud.objects.count(), 2)
        self.assertEqual(EntregaInvitacionContinuacion.objects.count(), 1)
        self.assertEqual(len(RecordingInvitationDeliveryBackend.deliveries), 1)

    @override_settings(
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_FALLA,
    )
    def test_recuperacion_reemplaza_enlace_y_deja_una_invitacion_valida(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._crear()
        enlace_anterior = (
            FailingInvitationDeliveryBackend.deliveries[0]['continuation_url']
        )

        RecordingInvitationDeliveryBackend.reset()
        with override_settings(
            FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_EXITO,
            FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS=0,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                call_command(
                    'procesar_entregas_invitacion',
                    stdout=StringIO(),
                )
            procesar_siguiente_correo()

        entregas = list(
            EntregaInvitacionContinuacion.objects.order_by('secuencia')
        )
        self.assertEqual(len(entregas), 2)
        self.assertEqual(
            entregas[0].estado,
            EstadoEntregaInvitacion.SUPERSEDED,
        )
        self.assertEqual(entregas[1].estado, EstadoEntregaInvitacion.SENT)
        self.assertEqual(entregas[1].reemplaza_a, entregas[0])
        self.assertEqual(
            InvitacionContinuacionSolicitud.objects.filter(
                estado=EstadoInvitacionContinuacion.ACTIVE,
            ).count(),
            1,
        )
        enlace_nuevo = (
            RecordingInvitationDeliveryBackend.deliveries[0][
                'continuation_url'
            ]
        )
        self.assertNotEqual(enlace_anterior, enlace_nuevo)
        self.assertEqual(
            self.client.get(urlparse(enlace_anterior).path).status_code,
            status.HTTP_410_GONE,
        )
        self.assertEqual(
            self.client.get(urlparse(enlace_nuevo).path).status_code,
            status.HTTP_302_FOUND,
        )

    def test_base_de_datos_impide_dos_entregas_iniciales(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._crear()
        solicitud = SolicitudFinanciacionEducativa.objects.get()
        EntregaInvitacionContinuacion.objects.update(
            estado=EstadoEntregaInvitacion.SENT,
        )
        segunda = emitir_invitacion_continuacion(solicitud=solicitud)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EntregaInvitacionContinuacion.objects.create(
                    solicitud=solicitud,
                    invitacion=segunda.invitacion,
                    secuencia=2,
                    origen=OrigenEntregaInvitacion.INITIAL,
                    destinatario_hmac=calcular_hmac_destinatario(
                        solicitud.correo
                    ),
                )

    def test_no_persisten_ni_exponen_secretos_de_entrega(self):
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self._crear()
        entrega = EntregaInvitacionContinuacion.objects.get()
        enlace = RecordingInvitationDeliveryBackend.deliveries[0][
            'continuation_url'
        ]
        token = urlparse(enlace).path.rstrip('/').split('/')[-1]
        representacion = repr({
            campo.name: getattr(entrega, campo.name)
            for campo in entrega._meta.fields
            if campo.name not in {'solicitud', 'invitacion', 'reemplaza_a'}
        })
        eventos = repr(list(
            EventoInvitacionContinuacion.objects.values_list(
                'tipo',
                'metadata',
            )
        ))

        self.assertNotIn(PAYLOAD['email'], representacion)
        self.assertNotIn(PAYLOAD['email'], eventos)
        self.assertNotIn(token, representacion)
        self.assertNotIn(token, eventos)
        self.assertNotIn(enlace, representacion)
        self.assertNotIn(enlace, eventos)
        self.assertNotIn('continuation_url', respuesta.data)
        self.assertNotIn('token', respuesta.data)
        self.assertNotEqual(
            entrega.destinatario_hmac,
            sha256(PAYLOAD['email'].encode('utf-8')).hexdigest(),
        )
        self.assertEqual(len(entrega.destinatario_hmac), 64)
        nombres_campos = {campo.name for campo in entrega._meta.fields}
        self.assertFalse({
            'correo',
            'email',
            'token',
            'url',
            'mensaje',
            'message',
            'contenido',
        }.intersection(nombres_campos))

    def test_request_no_programa_callback_ni_persiste_url_o_token(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self._crear(procesar=False)

        self.assertEqual(callbacks, [])
        outbox = OutboxCorreoEducativo.objects.get()
        representacion = repr(outbox.contexto)
        self.assertNotIn('credito.example.com', representacion)
        self.assertNotIn('/continuar/', representacion)
        self.assertNotIn('token', representacion.lower())

    def test_solicitud_preexistente_no_se_procesa_al_repetir_api(self):
        datos = DatosSolicitudFinanciacion(
            referencia_externa=PAYLOAD['external_reference'],
            nombres=PAYLOAD['first_names'],
            apellidos=PAYLOAD['last_names'],
            celular=PAYLOAD['phone'],
            correo=PAYLOAD['email'],
            direccion=PAYLOAD['address'],
            valor_plan=Decimal(PAYLOAD['plan_value']),
            plazo_meses=PAYLOAD['term'],
            nombre_curso=PAYLOAD['course_type'],
            tipo_curso=PAYLOAD['course_type'],
            correlation_id='pre-fase6',
        )
        crear_solicitud_idempotente(
            institucion=self.institucion,
            clave_idempotencia='fase6-idem-001',
            datos=datos,
        )

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self._crear()

        self.assertEqual(respuesta.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(respuesta['Idempotent-Replayed'], 'true')
        self.assertFalse(InvitacionContinuacionSolicitud.objects.exists())
        self.assertFalse(EntregaInvitacionContinuacion.objects.exists())
        self.assertFalse(OutboxCorreoEducativo.objects.exists())
        self.assertFalse(RecordingInvitationDeliveryBackend.deliveries)

    def test_recorrido_api_registro_asociacion_y_terminos(self):
        version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='fase6-v1',
            titulo='Terminos Fase 6',
            contenido='Contenido contractual de prueba.',
            obligatorio=True,
        )
        publicar_version_terminos(version=version)
        with self.captureOnCommitCallbacks(execute=True):
            respuesta_api = self._crear()
        enlace = RecordingInvitationDeliveryBackend.deliveries[0][
            'continuation_url'
        ]

        inicio = self.client.get(urlparse(enlace).path)
        registro = self.client.post(
            reverse('financiacion_educativa_web:registro'),
            REGISTRO,
        )
        confirmacion = self.client.post(
            reverse('financiacion_educativa_web:confirmar')
        )
        solicitud = SolicitudFinanciacionEducativa.objects.get(
            pk=respuesta_api.data['application_id']
        )
        url_terminos = reverse(
            'financiacion_educativa_web:terminos',
            kwargs={'solicitud_id': solicitud.pk},
        )
        self.client.get(url_terminos)
        aceptacion = self.client.post(
            url_terminos,
            {'accepted_versions': [str(version.pk)]},
        )

        solicitud.refresh_from_db()
        invitacion = InvitacionContinuacionSolicitud.objects.get(
            solicitud=solicitud,
            estado=EstadoInvitacionContinuacion.CONSUMED,
        )
        self.assertEqual(respuesta_api.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(inicio.status_code, status.HTTP_302_FOUND)
        self.assertEqual(registro.status_code, status.HTTP_302_FOUND)
        self.assertEqual(confirmacion.status_code, status.HTTP_302_FOUND)
        self.assertEqual(aceptacion.status_code, status.HTTP_302_FOUND)
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        )
        self.assertEqual(solicitud.usuario.email, REGISTRO['email'])
        self.assertEqual(solicitud.correo, solicitud.usuario.email)
        self.assertEqual(
            invitacion.estado,
            EstadoInvitacionContinuacion.CONSUMED,
        )
        self.assertEqual(
            solicitud.consentimientos.filter(
                tipo=version.tipo,
                version_texto=version.version,
            ).count(),
            1,
        )
        self.assertEqual(
            list(
                solicitud.historial_estados.order_by('creado_en').values_list(
                    'estado_nuevo',
                    flat=True,
                )
            ),
            [
                EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
                EstadoSolicitudFinanciacion.PENDING_TERMS,
                EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
            ],
        )
        self.assertEqual(
            set(invitacion.eventos.values_list('tipo', flat=True)),
            {
                TipoEventoInvitacion.ISSUED,
                TipoEventoInvitacion.DELIVERY_SCHEDULED,
                TipoEventoInvitacion.DELIVERY_STARTED,
                TipoEventoInvitacion.DELIVERY_SENT,
                TipoEventoInvitacion.CONSUMED,
            },
        )

    def test_configuracion_aplica_valores_aprobados(self):
        self.assertEqual(
            settings.FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT,
            5,
        )
        self.assertEqual(
            settings.FINANCIACION_EDUCATIVA_INVITATION_REISSUE_WINDOW_HOURS,
            24,
        )
        self.assertEqual(
            settings.FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS,
            300,
        )

    def test_openapi_no_publica_enlace_ni_token(self):
        respuesta = self.client.get(
            reverse('api-schema'),
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)
        esquema = respuesta.json()
        ruta = '/api/v1/financiacion-educativa/solicitudes/'
        operacion = esquema['paths'][ruta]['post']
        serializado = repr(operacion).lower()
        self.assertNotIn('continuation_url', serializado)
        self.assertNotIn('continuation_token', serializado)

        esquema_respuesta = operacion['responses']['202']['content'][
            'application/json'
        ]['schema']
        self.assertNotIn('continuation_url', repr(esquema_respuesta))

    def test_replay_con_nueva_clave_tampoco_emite_otra_invitacion(self):
        with self.captureOnCommitCallbacks(execute=True):
            primera = self._crear(clave='fase6-primera')
        with self.captureOnCommitCallbacks(execute=True):
            segunda = self._crear(clave='fase6-segunda')

        self.assertEqual(
            primera.data['application_id'],
            segunda.data['application_id'],
        )
        self.assertEqual(RegistroIdempotenciaSolicitud.objects.count(), 2)
        self.assertEqual(EntregaInvitacionContinuacion.objects.count(), 1)
        self.assertEqual(len(RecordingInvitationDeliveryBackend.deliveries), 1)

    def test_rollback_descarta_invitacion_entrega_y_callback(self):
        solicitud = SolicitudFinanciacionEducativa.objects.create(
            institucion=self.institucion,
            referencia_externa='FASE6-ROLLBACK',
            nombres='ANA',
            apellidos='PEREZ',
            celular='3001234567',
            correo='rollback@example.com',
            direccion='Calle 1',
            valor_plan=Decimal('1000000'),
            plazo_meses=6,
            nombre_curso='Curso',
            tipo_curso='Curso',
        )

        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    programar_invitacion_inicial(solicitud=solicitud)
                    raise RuntimeError('Forzar rollback de prueba.')

        self.assertFalse(InvitacionContinuacionSolicitud.objects.exists())
        self.assertFalse(EntregaInvitacionContinuacion.objects.exists())
        self.assertFalse(RecordingInvitationDeliveryBackend.deliveries)

    def test_cooldown_y_limite_restringen_reemisiones(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._crear()
        solicitud = SolicitudFinanciacionEducativa.objects.get()

        with self.assertRaises(ValidationError):
            reemitir_invitacion_orquestada(
                solicitud=solicitud,
                origen=OrigenEntregaInvitacion.AUTOMATIC_RETRY,
            )

        with override_settings(
            FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS=0,
            FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT=2,
        ):
            for _ in range(2):
                with self.captureOnCommitCallbacks(execute=True):
                    reemitir_invitacion_orquestada(
                        solicitud=solicitud,
                        origen=OrigenEntregaInvitacion.AUTOMATIC_RETRY,
                    )
            with self.assertRaises(ValidationError):
                reemitir_invitacion_orquestada(
                    solicitud=solicitud,
                    origen=OrigenEntregaInvitacion.AUTOMATIC_RETRY,
                )

        self.assertEqual(EntregaInvitacionContinuacion.objects.count(), 3)
        self.assertEqual(
            InvitacionContinuacionSolicitud.objects.filter(
                estado=EstadoInvitacionContinuacion.ACTIVE,
            ).count(),
            1,
        )
