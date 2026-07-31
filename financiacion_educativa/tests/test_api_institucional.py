from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import (
    CondicionesFinancieras,
    DocumentoFinanciacion,
    ParticipanteFinanciacion,
    RegistroIdempotenciaSolicitud,
    SolicitudFinanciacionEducativa,
)
from instituciones.models import CredencialAPIInstitucion, Institucion
from instituciones.services.credenciales import crear_credencial_api


PAYLOAD_VALIDO = {
    'external_reference': 'CURSO-2026-000123',
    'first_names': 'JUAN DAVID',
    'last_names': 'PEREZ GOMEZ',
    'phone': '3001234567',
    'email': 'juan@example.com',
    'address': 'Calle 10 # 20-30',
    'document_type': 'CC',
    'document_number': '0012345678',
    'birth_date': '2002-08-15',
    'enrollment_code': 'A2D-2026-00123',
    'academic_period': '2026-2',
    'campus': 'Sede Centro',
    'schedule': 'Nocturna',
    'program_name': 'INGLÉS BÁSICO A2 DIAMANTE',
    'enrollment_date': None,
    'plan_value': '2500000.00',
    'term': 6,
}


class APIInstitucionalFinanciacionTests(APITestCase):
    def setUp(self):
        self.institucion = self._crear_institucion('1')
        self.emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Pruebas',
        )
        self.url_crear = reverse(
            'financiacion_educativa_api:solicitud-crear'
        )

    def _crear_institucion(self, sufijo):
        return Institucion.objects.create(
            nombre_comercial=f'Institucion {sufijo}',
            razon_social=f'Institucion {sufijo} SAS',
            numero_identificacion_tributaria=f'90100000{sufijo}',
        )

    def _headers(self, token=None, clave='idem-0001'):
        return {
            'HTTP_AUTHORIZATION': f'ApiKey {token or self.emitida.token}',
            'HTTP_IDEMPOTENCY_KEY': clave,
        }

    def _crear(self, payload=None, token=None, clave='idem-0001'):
        return self.client.post(
            self.url_crear,
            data=payload or deepcopy(PAYLOAD_VALIDO),
            format='json',
            **self._headers(token=token, clave=clave),
        )

    def test_creacion_autenticada_devuelve_202_y_estado_inicial(self):
        respuesta = self._crear()

        self.assertEqual(respuesta.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            respuesta.data['status'],
            'RECEIVED',
        )
        self.assertFalse(respuesta.data['course_authorized'])
        solicitud = SolicitudFinanciacionEducativa.objects.get()
        self.assertEqual(str(solicitud.id), str(respuesta.data['application_id']))
        self.assertIsNone(solicitud.usuario)
        self.assertEqual(solicitud.numero_documento_estudiante, '0012345678')
        self.assertEqual(
            solicitud.nombre_curso,
            'INGLÉS BÁSICO A2 DIAMANTE',
        )
        self.assertIsNone(solicitud.fecha_matricula)
        self.assertEqual(respuesta.data['document_number'], '0012345678')
        self.assertIsNone(respuesta.data['enrollment_date'])

    def test_institucion_proviene_de_credencial_y_campo_es_rechazado(self):
        payload = deepcopy(PAYLOAD_VALIDO)
        payload['institution_id'] = str(uuid4())

        respuesta = self._crear(payload=payload)

        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('institution_id', respuesta.data['error']['fields'])
        self.assertFalse(SolicitudFinanciacionEducativa.objects.exists())

    def test_sin_credencial_devuelve_401(self):
        respuesta = self.client.post(
            self.url_crear,
            data=PAYLOAD_VALIDO,
            format='json',
            HTTP_IDEMPOTENCY_KEY='idem-sin-auth',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            respuesta.data['error']['code'],
            'AUTHENTICATION_REQUIRED',
        )

    def test_credencial_incorrecta_es_rechazada(self):
        prefijo = self.emitida.credencial.prefijo_clave

        respuesta = self._crear(token=f'{prefijo}.secreto-incorrecto')

        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(respuesta.data['error']['code'], 'INVALID_CREDENTIAL')

    def test_credencial_revocada_es_rechazada(self):
        CredencialAPIInstitucion.objects.filter(
            pk=self.emitida.credencial.pk
        ).update(activa=False)

        respuesta = self._crear()

        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(respuesta.data['error']['code'], 'CREDENTIAL_INACTIVE')

    def test_credencial_vencida_es_rechazada(self):
        CredencialAPIInstitucion.objects.filter(
            pk=self.emitida.credencial.pk
        ).update(expira_en=timezone.now() - timedelta(seconds=1))

        respuesta = self._crear()

        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(respuesta.data['error']['code'], 'CREDENTIAL_INACTIVE')

    def test_institucion_inactiva_es_rechazada(self):
        Institucion.objects.filter(pk=self.institucion.pk).update(activa=False)

        respuesta = self._crear()

        self.assertEqual(respuesta.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(respuesta.data['error']['code'], 'INSTITUTION_INACTIVE')

    def test_autenticacion_actualiza_ultimo_uso(self):
        self.assertIsNone(self.emitida.credencial.ultimo_uso_en)

        self._crear()

        self.emitida.credencial.refresh_from_db()
        self.assertIsNotNone(self.emitida.credencial.ultimo_uso_en)

    def test_valida_campos_obligatorios(self):
        payload = deepcopy(PAYLOAD_VALIDO)
        payload.pop('email')

        respuesta = self._crear(payload=payload)

        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(respuesta.data['error']['code'], 'VALIDATION_ERROR')
        self.assertIn('email', respuesta.data['error']['fields'])

    def test_rechaza_valor_y_plazo_no_positivos(self):
        casos = (
            ('plan_value', '0.00'),
            ('plan_value', '-1.00'),
            ('term', 0),
            ('term', -1),
        )
        for indice, (campo, valor) in enumerate(casos):
            with self.subTest(campo=campo, valor=valor):
                payload = deepcopy(PAYLOAD_VALIDO)
                payload[campo] = valor
                respuesta = self._crear(
                    payload=payload,
                    clave=f'idem-no-positivo-{indice}',
                )
                self.assertEqual(
                    respuesta.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertIn(campo, respuesta.data['error']['fields'])

    def test_rechaza_valor_monetario_numerico_para_evitar_float(self):
        payload = deepcopy(PAYLOAD_VALIDO)
        payload['plan_value'] = 2500000.00

        respuesta = self._crear(payload=payload)

        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('plan_value', respuesta.data['error']['fields'])

    def test_rechaza_campos_desconocidos(self):
        payload = deepcopy(PAYLOAD_VALIDO)
        payload['unexpected'] = 'no permitido'

        respuesta = self._crear(payload=payload)

        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('unexpected', respuesta.data['error']['fields'])

    def test_identidad_debe_enviarse_completa_y_con_fecha_valida(self):
        parcial = deepcopy(PAYLOAD_VALIDO)
        parcial.pop('birth_date')
        respuesta = self._crear(payload=parcial, clave='identidad-parcial')
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('document_type', respuesta.data['error']['fields'])

        futura = deepcopy(PAYLOAD_VALIDO)
        futura['birth_date'] = '2999-01-01'
        respuesta = self._crear(payload=futura, clave='fecha-futura')
        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('birth_date', respuesta.data['error']['fields'])

    def test_fecha_matricula_no_puede_ser_informada_por_la_institucion(self):
        payload = deepcopy(PAYLOAD_VALIDO)
        payload['enrollment_date'] = '2026-07-26'

        respuesta = self._crear(payload=payload, clave='fecha-matricula')

        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('enrollment_date', respuesta.data['error']['fields'])
        self.assertFalse(SolicitudFinanciacionEducativa.objects.exists())

    def test_course_type_se_conserva_como_alias_compatible(self):
        payload = deepcopy(PAYLOAD_VALIDO)
        payload.pop('program_name')
        payload['course_type'] = 'INGLÉS BÁSICO A2 DIAMANTE'

        respuesta = self._crear(payload=payload, clave='alias-programa')

        self.assertEqual(respuesta.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            respuesta.data['program_name'],
            'INGLÉS BÁSICO A2 DIAMANTE',
        )

    def test_clave_y_payload_iguales_no_duplican(self):
        primera = self._crear()
        segunda = self._crear()

        self.assertEqual(primera.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(segunda.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            primera.data['application_id'],
            segunda.data['application_id'],
        )
        self.assertEqual(segunda['Idempotent-Replayed'], 'true')
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)
        self.assertEqual(RegistroIdempotenciaSolicitud.objects.count(), 1)
        registro = RegistroIdempotenciaSolicitud.objects.get()
        self.assertEqual(len(registro.clave_hash), 64)
        self.assertEqual(len(registro.payload_hash), 64)
        self.assertNotIn('idem-0001', registro.clave_hash)
        campos_idempotencia = {
            campo.name
            for campo in RegistroIdempotenciaSolicitud._meta.fields
        }
        self.assertFalse({
            'secret',
            'email',
            'address',
            'payload',
        }.intersection(campos_idempotencia))

    def test_referencia_compatible_con_otra_clave_reutiliza_solicitud(self):
        primera = self._crear(clave='clave-uno')
        segunda = self._crear(clave='clave-dos')

        self.assertEqual(primera.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(segunda.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            primera.data['application_id'],
            segunda.data['application_id'],
        )
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)
        self.assertEqual(RegistroIdempotenciaSolicitud.objects.count(), 2)

    def test_clave_igual_con_payload_distinto_devuelve_409(self):
        self._crear()
        payload = deepcopy(PAYLOAD_VALIDO)
        payload['term'] = 12

        respuesta = self._crear(payload=payload)

        self.assertEqual(respuesta.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            respuesta.data['error']['code'],
            'IDEMPOTENCY_CONFLICT',
        )
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)

    def test_idempotencia_se_aisla_por_institucion(self):
        otra = self._crear_institucion('2')
        otra_emitida = crear_credencial_api(
            institucion=otra,
            nombre='Pruebas',
        )

        primera = self._crear(clave='clave-compartida')
        segunda = self._crear(
            token=otra_emitida.token,
            clave='clave-compartida',
        )

        self.assertEqual(primera.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(segunda.status_code, status.HTTP_202_ACCEPTED)
        self.assertNotEqual(
            primera.data['application_id'],
            segunda.data['application_id'],
        )
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 2)

    def test_referencia_duplicada_incompatible_devuelve_409(self):
        self._crear(clave='clave-original')
        payload = deepcopy(PAYLOAD_VALIDO)
        payload['plan_value'] = '3000000.00'

        respuesta = self._crear(
            payload=payload,
            clave='clave-nueva',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            respuesta.data['error']['code'],
            'EXTERNAL_REFERENCE_CONFLICT',
        )
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)

    def test_consulta_solicitud_propia(self):
        creada = self._crear()
        url = reverse(
            'financiacion_educativa_api:solicitud-detalle',
            kwargs={'application_id': creada.data['application_id']},
        )

        respuesta = self.client.get(
            url,
            HTTP_AUTHORIZATION=f'ApiKey {self.emitida.token}',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)
        self.assertEqual(
            respuesta.data['external_reference'],
            PAYLOAD_VALIDO['external_reference'],
        )
        self.assertEqual(respuesta.data['address'], PAYLOAD_VALIDO['address'])
        self.assertEqual(respuesta.data['email'], PAYLOAD_VALIDO['email'])
        self.assertEqual(
            respuesta.data['document_number'],
            PAYLOAD_VALIDO['document_number'],
        )
        self.assertEqual(
            respuesta.data['program_name'],
            PAYLOAD_VALIDO['program_name'],
        )
        self.assertEqual(respuesta.data['plan_value'], '2500000.00')

    def test_otra_institucion_no_puede_consultar_solicitud(self):
        creada = self._crear()
        otra = self._crear_institucion('2')
        otra_emitida = crear_credencial_api(
            institucion=otra,
            nombre='Pruebas',
        )
        url = reverse(
            'financiacion_educativa_api:solicitud-detalle',
            kwargs={'application_id': creada.data['application_id']},
        )

        respuesta = self.client.get(
            url,
            HTTP_AUTHORIZATION=f'ApiKey {otra_emitida.token}',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(respuesta.data['error']['code'], 'NOT_FOUND')

    def test_creacion_no_genera_participantes_documentos_condiciones_o_desembolsos(self):
        self._crear()

        self.assertFalse(ParticipanteFinanciacion.objects.exists())
        self.assertFalse(DocumentoFinanciacion.objects.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())
        campos = {
            campo.name for campo in SolicitudFinanciacionEducativa._meta.fields
        }
        self.assertFalse({
            'desembolso',
            'transferencia',
            'monto_desembolsado',
        }.intersection(campos))

    def test_clave_idempotencia_es_obligatoria(self):
        respuesta = self.client.post(
            self.url_crear,
            data=PAYLOAD_VALIDO,
            format='json',
            HTTP_AUTHORIZATION=f'ApiKey {self.emitida.token}',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            'Idempotency-Key',
            respuesta.data['error']['fields'],
        )

    def test_esquema_openapi_documenta_api_y_autenticacion(self):
        respuesta = self.client.get(
            reverse('api-schema'),
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(respuesta.status_code, status.HTTP_200_OK)
        esquema = respuesta.json()
        ruta_creacion = (
            '/api/v1/financiacion-educativa/solicitudes/'
        )
        self.assertIn(ruta_creacion, esquema['paths'])
        self.assertIn(
            'InstitutionApiKey',
            esquema['components']['securitySchemes'],
        )
        parametros = esquema['paths'][ruta_creacion]['post']['parameters']
        self.assertTrue(any(
            parametro['name'] == 'Idempotency-Key'
            for parametro in parametros
        ))
        self.assertIn(
            '202',
            esquema['paths'][ruta_creacion]['post']['responses'],
        )
        encabezados_202 = esquema['paths'][ruta_creacion]['post'][
            'responses'
        ]['202']['headers']
        self.assertIn('Idempotent-Replayed', encabezados_202)
        esquema_entrada = esquema['components']['schemas'][
            'CrearSolicitud'
        ]
        for campo in (
            'document_type',
            'document_number',
            'birth_date',
            'enrollment_code',
            'academic_period',
            'campus',
            'schedule',
            'program_name',
            'enrollment_date',
        ):
            self.assertIn(campo, esquema_entrada['properties'])
        terminos = esquema['components']['schemas'][
            'TerminosFinancieros'
        ]
        self.assertEqual(
            set(terminos['required']),
            {
                'currency',
                'requested_amount',
                'financed_amount',
                'term_months',
                'estimated_installment',
            },
        )
        self.assertNotIn('additionalProperties', terminos)
