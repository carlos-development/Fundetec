import json
import time

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gestion_creditos.models import (
    Empresa,
    WhatsAppInternalAPIAuditLog,
    WhatsAppInternalApplication,
    WhatsAppInternalConsent,
)
from gestion_creditos.services.credit_simulation import calculate_credit_simulation
from gestion_creditos.tests.whatsapp_internal_api_fixtures import (
    PAYROLL_DOCUMENT,
    PAYROLL_PHONE,
    WHATSAPP_DOCUMENT,
    WHATSAPP_PHONE,
    create_payroll_loan_fixture,
    create_whatsapp_credit_fixture,
    payroll_application_payload,
    whatsapp_application_payload,
)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
    WHATSAPP_INTERNAL_API_KEY='test-internal-key',
    WHATSAPP_INTERNAL_API_RATE_LIMIT=0,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class WhatsAppInternalAPITests(TestCase):
    def _post_json(self, url_name, payload, key='test-internal-key'):
        return self.client.post(
            reverse(url_name),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_INTERNAL_API_KEY=key,
        )

    def _get(self, url_name, params=None, key='test-internal-key'):
        return self.client.get(reverse(url_name), params or {}, HTTP_X_INTERNAL_API_KEY=key)

    def _assert_metadata_contains(self, metadata, expected):
        for key, value in expected.items():
            self.assertEqual(metadata.get(key), value)

    def _assert_normalized_media_metadata(self, media_metadata):
        self.assertEqual(set(media_metadata.keys()), {'bank_certificate', 'id_front', 'id_back'})
        for field_name, item in media_metadata.items():
            self.assertEqual(set(item.keys()), {'media_id', 'filename', 'mime_type', 'field_name', 'received_at'})
            self.assertEqual(item['field_name'], field_name)
            self.assertIsNotNone(parse_datetime(item['received_at']))
            self.assertNotIn('url', item)
            self.assertNotIn('download_url', item)

    def test_all_internal_endpoints_require_api_key(self):
        endpoint_calls = [
            ('get', 'internal_whatsapp:products', None),
            ('post', 'internal_whatsapp:simulations', {}),
            ('post', 'internal_whatsapp:applications', {}),
            ('post', 'internal_whatsapp:payroll_applications', {}),
            ('get', 'internal_whatsapp:application_status', {'document_number': WHATSAPP_DOCUMENT}),
            ('get', 'internal_whatsapp:credit_status', {'document_number': PAYROLL_DOCUMENT}),
            ('get', 'internal_whatsapp:documents', {'document_number': WHATSAPP_DOCUMENT}),
            ('post', 'internal_whatsapp:identity_validate', {}),
            ('post', 'internal_whatsapp:consents', {}),
        ]

        for method, url_name, payload in endpoint_calls:
            with self.subTest(url_name=url_name):
                url = reverse(url_name)
                if method == 'get':
                    response = self.client.get(url, payload or {})
                else:
                    response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {'error': 'API key requerida o invalida.'})

    def test_application_rejects_invalid_api_key(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(),
            key='invalid-key',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'API key requerida o invalida.'})
        self.assertFalse(WhatsAppInternalApplication.objects.exists())

    def test_all_internal_endpoints_accept_x_internal_api_key_header(self):
        whatsapp_application = create_whatsapp_credit_fixture()
        payroll = create_payroll_loan_fixture()
        endpoint_calls = [
            ('get', 'internal_whatsapp:products', None, 200),
            ('post', 'internal_whatsapp:simulations', {
                'product_type': 'whatsapp_credit',
                'amount': '1000000',
                'term_months': 6,
                'phone': WHATSAPP_PHONE,
                'document_number': WHATSAPP_DOCUMENT,
            }, 200),
            ('post', 'internal_whatsapp:applications', whatsapp_application_payload(numero_documento='1009999999'), 201),
            ('post', 'internal_whatsapp:payroll_applications', payroll_application_payload(
                numero_documento='1007654321',
                empresa_id=payroll['empresa'].id,
            ), 201),
            ('get', 'internal_whatsapp:application_status', {
                'document_number': whatsapp_application.numero_documento,
                'product_type': 'whatsapp_credit',
            }, 200),
            ('get', 'internal_whatsapp:credit_status', {
                'document_number': PAYROLL_DOCUMENT,
                'product_type': 'payroll_loan',
            }, 200),
            ('get', 'internal_whatsapp:documents', {
                'document_number': WHATSAPP_DOCUMENT,
                'product_type': 'whatsapp_credit',
            }, 200),
            ('post', 'internal_whatsapp:identity_validate', {
                'document_number': WHATSAPP_DOCUMENT,
                'phone': WHATSAPP_PHONE,
            }, 200),
            ('post', 'internal_whatsapp:consents', {
                'product_type': 'whatsapp_credit',
                'document_number': WHATSAPP_DOCUMENT,
                'phone': WHATSAPP_PHONE,
                'consent_type': 'tratamiento_datos',
                'accepted': True,
                'text_version': 'v1',
            }, 201),
        ]

        for method, url_name, payload, expected_status in endpoint_calls:
            with self.subTest(url_name=url_name):
                response = self._get(url_name, payload) if method == 'get' else self._post_json(url_name, payload)
                self.assertEqual(response.status_code, expected_status)

    def test_products_contract_matches_documentation(self):
        response = self._get('internal_whatsapp:products', {'product_type': 'payroll_loan'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'products': [
                {
                    'product_type': 'payroll_loan',
                    'name': 'Credito de libranza',
                    'description': 'Credito de libranza para empleados de empresas con convenio activo.',
                    'current_flow': 'aprobado.com.co/libranza/',
                    'monthly_rate': '1.9',
                    'origination_rate': '10',
                    'vat_rate': '19',
                }
            ]
        })

    def test_simulation_returns_financial_contract(self):
        response = self._post_json(
            'internal_whatsapp:simulations',
            {
                'product_type': 'whatsapp_credit',
                'amount': '1000000',
                'term_months': 6,
                'phone': WHATSAPP_PHONE,
                'document_number': WHATSAPP_DOCUMENT,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'amount': '1000000.00',
            'term_months': 6,
            'origination_fee': '100000.00',
            'vat': '19000.00',
            'interest': '141004.38',
            'total_to_pay': '1260004.38',
            'monthly_payment': '210000.73',
            'valid_until': (timezone.localdate() + timezone.timedelta(days=7)).isoformat(),
            'warnings': [],
        })

    def test_simulation_matches_official_backend_simulator_for_whatsapp_credit(self):
        payload = {
            'product_type': 'whatsapp_credit',
            'amount': '2000000',
            'term_months': 6,
            'phone': WHATSAPP_PHONE,
            'document_number': WHATSAPP_DOCUMENT,
        }

        response = self._post_json('internal_whatsapp:simulations', payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            calculate_credit_simulation(
                product_type='whatsapp_credit',
                amount='2000000',
                term_months=6,
                document_number=WHATSAPP_DOCUMENT,
            ),
        )

    def test_payroll_simulation_keeps_product_separation_warning(self):
        response = self._post_json(
            'internal_whatsapp:simulations',
            {
                'product_type': 'payroll_loan',
                'amount': '1000000',
                'term_months': 6,
                'phone': PAYROLL_PHONE,
                'document_number': PAYROLL_DOCUMENT,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['warnings'], [
            'La libranza requiere convenio activo del pagador y validacion laboral.'
        ])

    def test_payroll_simulation_matches_real_web_simulator_case(self):
        response = self._post_json(
            'internal_whatsapp:simulations',
            {
                'product_type': 'payroll_loan',
                'amount': '3000000',
                'term_months': 6,
                'phone': PAYROLL_PHONE,
                'document_number': PAYROLL_DOCUMENT,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'amount': '3000000.00',
            'term_months': 6,
            'origination_fee': '300000.00',
            'vat': '57000.00',
            'interest': '226741.20',
            'total_to_pay': '3583741.20',
            'monthly_payment': '597290.20',
            'valid_until': (timezone.localdate() + timezone.timedelta(days=7)).isoformat(),
            'warnings': ['La libranza requiere convenio activo del pagador y validacion laboral.'],
        })

    def test_simulation_rejects_product_limits_before_calculating(self):
        cases = [
            (
                {
                    'product_type': 'whatsapp_credit',
                    'amount': '299999.99',
                    'term_months': 6,
                    'phone': WHATSAPP_PHONE,
                    'document_number': WHATSAPP_DOCUMENT,
                },
                {'amount': 'El monto minimo para whatsapp_credit es 300000.'},
            ),
            (
                {
                    'product_type': 'whatsapp_credit',
                    'amount': '2000000.01',
                    'term_months': 7,
                    'phone': WHATSAPP_PHONE,
                    'document_number': WHATSAPP_DOCUMENT,
                },
                {
                    'amount': 'El monto maximo para whatsapp_credit es 2000000.',
                    'term_months': 'El plazo maximo para whatsapp_credit es 6 meses.',
                },
            ),
            (
                {
                    'product_type': 'payroll_loan',
                    'amount': '499999.99',
                    'term_months': 6,
                    'phone': PAYROLL_PHONE,
                    'document_number': PAYROLL_DOCUMENT,
                },
                {'amount': 'El monto minimo para payroll_loan es 500000.'},
            ),
            (
                {
                    'product_type': 'payroll_loan',
                    'amount': '3000000.01',
                    'term_months': 7,
                    'phone': PAYROLL_PHONE,
                    'document_number': PAYROLL_DOCUMENT,
                },
                {
                    'amount': 'El monto maximo para payroll_loan es 3000000.',
                    'term_months': 'El plazo maximo para payroll_loan es 6 meses.',
                },
            ),
        ]

        for payload, errors in cases:
            with self.subTest(product_type=payload['product_type'], amount=payload['amount']):
                response = self._post_json('internal_whatsapp:simulations', payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'error': 'Datos invalidos.', 'errors': errors})

    def test_create_whatsapp_credit_application(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(),
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data, {
            'application_id': data['application_id'],
            'status': 'received',
            'next_step': 'risk_prevalidation',
            'message': 'Solicitud recibida para validacion inicial del credito por WhatsApp.',
        })
        application = WhatsAppInternalApplication.objects.get(id=data['application_id'])
        self.assertEqual(application.product_type, WhatsAppInternalApplication.ProductType.WHATSAPP_CREDIT)
        self.assertEqual(application.source, 'whatsapp')

    def test_create_whatsapp_credit_application_with_normalized_flow_payload(self):
        payload = whatsapp_application_payload()
        payload.pop('ciudad')
        payload.pop('ocupacion')

        response = self._post_json('internal_whatsapp:applications', payload)

        self.assertEqual(response.status_code, 201)
        application = WhatsAppInternalApplication.objects.get(id=response.json()['application_id'])
        self.assertEqual(application.ciudad, '')
        self.assertEqual(application.ocupacion, '')
        self.assertEqual(application.metadata['source_payload_version'], 'whatsapp_flow_v1')
        self.assertEqual(application.metadata['direccion'], 'Calle 123 #45-67')
        self.assertEqual(application.metadata['media_processing'], 'pending_not_downloaded')
        self._assert_normalized_media_metadata(application.metadata['media_metadata'])

    def test_whatsapp_credit_rejects_amount_and_term_outside_flow_limits(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(monto_solicitado='2000000.01', plazo_meses=7),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {
                'monto_solicitado': 'El monto maximo para whatsapp_credit es 2000000.',
                'plazo_meses': 'El plazo maximo para whatsapp_credit es 6 meses.',
            },
        })

    def test_whatsapp_credit_rejects_amount_below_flow_minimum(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(monto_solicitado='299999.99'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'monto_solicitado': 'El monto minimo para whatsapp_credit es 300000.'},
        })

    def test_application_rejects_invalid_product_type(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(product_type='unknown_credit'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'product_type': 'Use whatsapp_credit o payroll_loan.'},
        })

    def test_whatsapp_application_endpoint_rejects_payroll_product(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            payroll_application_payload(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'product_type': 'Use el endpoint separado de libranza para payroll_loan.'},
        })

    def test_payroll_application_uses_separate_endpoint_and_validates_company(self):
        payroll = create_payroll_loan_fixture()

        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            payroll_application_payload(empresa_id=payroll['empresa'].id),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {
            'application_id': response.json()['application_id'],
            'status': 'pending_form_completion',
            'next_step': 'continue_existing_libranza_flow',
            'message': 'Solicitud de libranza iniciada. Debe continuar el flujo existente de formulario, documentos y pagare.',
        })
        application = WhatsAppInternalApplication.objects.get(id=response.json()['application_id'])
        self.assertEqual(application.product_type, WhatsAppInternalApplication.ProductType.PAYROLL_LOAN)
        self.assertTrue(application.convenio_validado)
        self.assertTrue(application.vinculo_laboral_validado)
        self._assert_normalized_media_metadata(application.metadata['media_metadata'])
        self.assertEqual(application.metadata['media_processing'], 'pending_not_downloaded')
        self.assertEqual(application.metadata['payroll_validation'], {
            'empresa_found': True,
            'empresa_convenio_activo': True,
            'empresa_tipo_valido': True,
            'vinculo_laboral_validado': True,
            'ready_for_existing_flow': True,
            'pending_reasons': [],
        })

    def test_payroll_application_accepts_flow_payload_with_company_name(self):
        payroll = create_payroll_loan_fixture()
        payload = payroll_application_payload(empresa_nombre=payroll['empresa'].nombre)
        payload.pop('ciudad')
        payload.pop('ocupacion')

        response = self._post_json('internal_whatsapp:payroll_applications', payload)

        self.assertEqual(response.status_code, 201)
        application = WhatsAppInternalApplication.objects.get(id=response.json()['application_id'])
        self.assertEqual(application.product_type, WhatsAppInternalApplication.ProductType.PAYROLL_LOAN)
        self.assertEqual(application.empresa, payroll['empresa'])
        self.assertEqual(application.metadata['direccion'], 'Calle 123 #45-67')

    def test_payroll_application_creates_controlled_staging_when_link_is_not_validated(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Sin Vinculo Validado',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )

        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            payroll_application_payload(empresa_id=empresa.id),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {
            'application_id': response.json()['application_id'],
            'status': 'pending_payroll_validation',
            'next_step': 'pending_payroll_validation',
            'message': 'Solicitud de libranza recibida para validacion de convenio y vinculo laboral.',
        })
        application = WhatsAppInternalApplication.objects.get(id=response.json()['application_id'])
        self.assertEqual(application.status, WhatsAppInternalApplication.Status.PENDING_PAYROLL_VALIDATION)
        self.assertTrue(application.convenio_validado)
        self.assertFalse(application.vinculo_laboral_validado)
        self.assertEqual(application.metadata['payroll_validation']['pending_reasons'], ['vinculo_laboral_no_validado'])

    def test_payroll_application_creates_controlled_staging_when_company_cannot_be_validated(self):
        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            payroll_application_payload(empresa_nombre='Empresa No Existe'),
        )

        self.assertEqual(response.status_code, 201)
        application = WhatsAppInternalApplication.objects.get(id=response.json()['application_id'])
        self.assertEqual(application.status, WhatsAppInternalApplication.Status.PENDING_PAYROLL_VALIDATION)
        self.assertIsNone(application.empresa)
        self.assertFalse(application.convenio_validado)
        self.assertEqual(application.metadata['payroll_validation']['pending_reasons'], ['empresa_no_encontrada'])

    def test_payroll_application_rejects_missing_company_reference(self):
        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            payroll_application_payload(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'empresa': 'Debe enviar empresa_id o empresa_nombre.'},
        })

    def test_payroll_application_requires_payroll_product_type(self):
        payroll = create_payroll_loan_fixture()

        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            whatsapp_application_payload(empresa_id=payroll['empresa'].id),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'product_type': 'Use payroll_loan en este endpoint.'},
        })

    def test_payroll_application_rejects_amount_and_term_outside_flow_limits(self):
        payroll = create_payroll_loan_fixture()

        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            payroll_application_payload(
                empresa_id=payroll['empresa'].id,
                monto_solicitado='3000000.01',
                plazo_meses=7,
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {
                'monto_solicitado': 'El monto maximo para payroll_loan es 3000000.',
                'plazo_meses': 'El plazo maximo para payroll_loan es 6 meses.',
            },
        })

    def test_payroll_application_rejects_amount_below_flow_minimum(self):
        payroll = create_payroll_loan_fixture()

        response = self._post_json(
            'internal_whatsapp:payroll_applications',
            payroll_application_payload(empresa_id=payroll['empresa'].id, monto_solicitado='499999.99'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'monto_solicitado': 'El monto minimo para payroll_loan es 500000.'},
        })

    def test_application_rejects_missing_consents(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(
                autorizacion_tratamiento_datos=False,
                autorizacion_validacion_informacion=False,
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {
                'autorizacion_tratamiento_datos': 'Debe ser true.',
                'autorizacion_validacion_informacion': 'Debe ser true.',
            },
        })

    def test_application_rejects_malformed_media_metadata(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(media_metadata={'bank_certificate': 'wamid.bank.123'}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'media_metadata.bank_certificate': 'Debe ser un objeto JSON.'},
        })
        self.assertFalse(WhatsAppInternalApplication.objects.exists())

    def test_application_rejects_media_metadata_without_media_id(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(media_metadata={'bank_certificate': {'filename': 'certificado.pdf'}}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'media_metadata.bank_certificate.media_id': 'Este campo es obligatorio.'},
        })
        self.assertFalse(WhatsAppInternalApplication.objects.exists())

    def test_media_metadata_filters_public_urls_and_keeps_safe_fields_only(self):
        payload = whatsapp_application_payload()
        payload['media_metadata']['bank_certificate']['url'] = 'https://example.com/sensitive.pdf'
        payload['media_metadata']['bank_certificate']['download_url'] = 'https://example.com/download'

        response = self._post_json('internal_whatsapp:applications', payload)

        self.assertEqual(response.status_code, 201)
        application = WhatsAppInternalApplication.objects.get(id=response.json()['application_id'])
        bank_certificate = application.metadata['media_metadata']['bank_certificate']
        self.assertEqual(set(bank_certificate.keys()), {'media_id', 'filename', 'mime_type', 'field_name', 'received_at'})
        self.assertNotIn('url', bank_certificate)
        self.assertNotIn('download_url', bank_certificate)

    def test_whatsapp_application_is_idempotent_for_bot_retries(self):
        payload = whatsapp_application_payload()

        first_response = self._post_json('internal_whatsapp:applications', payload)
        second_response = self._post_json('internal_whatsapp:applications', payload)

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json()['idempotent_replay'], True)
        self.assertEqual(second_response.json()['application_id'], first_response.json()['application_id'])
        self.assertEqual(
            WhatsAppInternalApplication.objects.filter(product_type='whatsapp_credit').count(),
            1,
        )

    def test_payroll_application_is_idempotent_for_bot_retries(self):
        payroll = create_payroll_loan_fixture()
        payload = payroll_application_payload(empresa_id=payroll['empresa'].id)

        first_response = self._post_json('internal_whatsapp:payroll_applications', payload)
        second_response = self._post_json('internal_whatsapp:payroll_applications', payload)

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json()['idempotent_replay'], True)
        self.assertEqual(second_response.json()['application_id'], first_response.json()['application_id'])
        self.assertEqual(
            WhatsAppInternalApplication.objects.filter(product_type='payroll_loan').count(),
            1,
        )

    def test_application_rejects_unknown_media_metadata_keys(self):
        payload = whatsapp_application_payload()
        payload['media_metadata']['selfie'] = {'media_id': 'wamid.selfie.123'}

        response = self._post_json('internal_whatsapp:applications', payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'media_metadata': 'Campos no soportados: selfie.'},
        })

    def test_application_status_for_whatsapp_application(self):
        application = create_whatsapp_credit_fixture()

        response = self._get(
            'internal_whatsapp:application_status',
            {'document_number': WHATSAPP_DOCUMENT, 'product_type': 'whatsapp_credit'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'application_id': application.id,
            'product_type': 'whatsapp_credit',
            'status': 'received',
            'status_label': 'Recibida',
            'source': 'whatsapp',
            'created_at': application.created_at.isoformat(),
            'next_step': 'risk_prevalidation',
        })

    def test_application_status_for_existing_payroll_credit(self):
        payroll = create_payroll_loan_fixture()

        response = self._get(
            'internal_whatsapp:application_status',
            {'document_number': PAYROLL_DOCUMENT, 'product_type': 'payroll_loan'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'application_id': payroll['credito'].id,
            'product_type': 'payroll_loan',
            'status': 'ACTIVO',
            'status_label': 'Activo',
            'source': 'aprobado_backend',
            'created_at': payroll['credito'].fecha_solicitud.isoformat(),
            'next_step': 'credit_active',
        })

    def test_application_status_does_not_mix_payroll_credit_into_whatsapp_credit(self):
        create_payroll_loan_fixture()

        response = self._get(
            'internal_whatsapp:application_status',
            {'document_number': PAYROLL_DOCUMENT, 'product_type': 'whatsapp_credit'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'Solicitud no encontrada.'})

    def test_application_status_does_not_mix_whatsapp_application_into_payroll_loan(self):
        create_whatsapp_credit_fixture()

        response = self._get(
            'internal_whatsapp:application_status',
            {'document_number': WHATSAPP_DOCUMENT, 'product_type': 'payroll_loan'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'Solicitud no encontrada.'})

    def test_whatsapp_credit_status_does_not_mix_with_payroll_credit(self):
        create_payroll_loan_fixture()

        response = self._get(
            'internal_whatsapp:credit_status',
            {'document_number': PAYROLL_DOCUMENT, 'product_type': 'whatsapp_credit'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'Credito activo no encontrado.'})

    def test_missing_required_application_data_returns_400(self):
        payload = whatsapp_application_payload()
        payload.pop('numero_documento')

        response = self._post_json('internal_whatsapp:applications', payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn('numero_documento', response.json()['errors'])

    def test_credit_status_returns_non_sensitive_payroll_data(self):
        payroll = create_payroll_loan_fixture()

        response = self._get(
            'internal_whatsapp:credit_status',
            {'document_number': PAYROLL_DOCUMENT, 'product_type': 'payroll_loan'},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, {
            'has_active_credit': True,
            'product_type': 'payroll_loan',
            'credit_reference': payroll['credito'].numero_credito,
            'status': 'ACTIVO',
            'status_label': 'Activo',
            'next_payment_date': '2026-06-01',
            'days_past_due': 0,
        })
        self.assertNotIn('saldo_pendiente', data)
        self.assertNotIn('monto_aprobado', data)
        self.assertNotIn('total_a_pagar', data)
        self.assertNotIn('valor_cuota', data)

    def test_documents_lists_metadata_without_file_delivery(self):
        create_whatsapp_credit_fixture()

        response = self._get(
            'internal_whatsapp:documents',
            {'document_number': WHATSAPP_DOCUMENT, 'product_type': 'whatsapp_credit'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'documents': [
                {
                    'product_type': 'whatsapp_credit',
                    'document_type': 'initial_application',
                    'label': 'Solicitud inicial',
                    'available': True,
                    'delivery': 'not_available_without_strong_identity_validation',
                }
            ]
        })
        self.assertNotIn('url', response.json()['documents'][0])
        self.assertNotIn('file', response.json()['documents'][0])

    def test_payroll_documents_do_not_deliver_files_or_sensitive_urls(self):
        create_payroll_loan_fixture()

        response = self._get(
            'internal_whatsapp:documents',
            {'document_number': PAYROLL_DOCUMENT, 'product_type': 'payroll_loan'},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['documents'][4], {
            'product_type': 'payroll_loan',
            'document_type': 'certificado_bancario',
            'label': 'Certificado bancario',
            'available': True,
            'delivery': 'not_available_without_strong_identity_validation',
        })
        for document in data['documents']:
            self.assertEqual(set(document.keys()), {'product_type', 'document_type', 'label', 'available', 'delivery'})
            self.assertEqual(document['delivery'], 'not_available_without_strong_identity_validation')

    def test_identity_validate_returns_token_on_basic_match(self):
        create_whatsapp_credit_fixture()

        response = self._post_json(
            'internal_whatsapp:identity_validate',
            {'document_number': WHATSAPP_DOCUMENT, 'phone': WHATSAPP_PHONE},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['identity_validated'], True)
        self.assertIsInstance(data['identity_token'], str)
        self.assertGreater(len(data['identity_token']), 20)
        self.assertEqual(data['expires_in_seconds'], 600)
        self.assertIsNotNone(cache.get(f"whatsapp-internal-identity:{data['identity_token']}"))

    @override_settings(WHATSAPP_INTERNAL_IDENTITY_TOKEN_SECONDS=1)
    def test_identity_token_expires_from_cache(self):
        create_whatsapp_credit_fixture()

        response = self._post_json(
            'internal_whatsapp:identity_validate',
            {'document_number': WHATSAPP_DOCUMENT, 'phone': WHATSAPP_PHONE},
        )

        self.assertEqual(response.status_code, 200)
        token = response.json()['identity_token']
        self.assertIsNotNone(cache.get(f"whatsapp-internal-identity:{token}"))

        time.sleep(1.2)

        self.assertIsNone(cache.get(f"whatsapp-internal-identity:{token}"))

    def test_identity_validate_returns_false_for_document_phone_mismatch(self):
        create_whatsapp_credit_fixture()

        response = self._post_json(
            'internal_whatsapp:identity_validate',
            {'document_number': WHATSAPP_DOCUMENT, 'phone': '3999999999'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'identity_validated': False,
            'identity_token': None,
            'expires_in_seconds': 0,
        })

    def test_identity_validate_rejects_invalid_phone_shape(self):
        response = self._post_json(
            'internal_whatsapp:identity_validate',
            {'document_number': WHATSAPP_DOCUMENT, 'phone': '123'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'error': 'Datos invalidos.',
            'errors': {'phone': 'Debe incluir al menos 7 digitos.'},
        })

    def test_identity_audit_masks_document_and_phone_without_token_or_raw_pii(self):
        create_whatsapp_credit_fixture()

        response = self._post_json(
            'internal_whatsapp:identity_validate',
            {'document_number': WHATSAPP_DOCUMENT, 'phone': WHATSAPP_PHONE},
        )

        self.assertEqual(response.status_code, 200)
        audit = WhatsAppInternalAPIAuditLog.objects.filter(action='identity_validate').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.document_number_masked, '******4567')
        self.assertEqual(audit.phone_masked, '******4567')
        self.assertNotEqual(audit.document_number_hash, WHATSAPP_DOCUMENT)
        audit_metadata = json.dumps(audit.metadata)
        self.assertNotIn(WHATSAPP_DOCUMENT, audit_metadata)
        self.assertNotIn(WHATSAPP_PHONE, audit_metadata)
        self.assertNotIn(response.json()['identity_token'], audit_metadata)
        self._assert_metadata_contains(audit.metadata, {
            'result': 'validated',
            'identity_validated': True,
            'expires_in_seconds': 600,
        })

    def test_identity_validate_does_not_unlock_sensitive_credit_status(self):
        create_payroll_loan_fixture()
        identity_response = self._post_json(
            'internal_whatsapp:identity_validate',
            {'document_number': PAYROLL_DOCUMENT, 'phone': PAYROLL_PHONE},
        )

        self.assertEqual(identity_response.status_code, 200)
        self.assertTrue(identity_response.json()['identity_validated'])

        status_response = self._get(
            'internal_whatsapp:credit_status',
            {'document_number': PAYROLL_DOCUMENT, 'product_type': 'payroll_loan'},
        )

        self.assertNotIn('saldo_pendiente', status_response.json())
        self.assertNotIn('monto_aprobado', status_response.json())

    def test_register_consent_and_audit_without_full_document_in_audit(self):
        response = self._post_json(
            'internal_whatsapp:consents',
            {
                'product_type': 'whatsapp_credit',
                'document_number': WHATSAPP_DOCUMENT,
                'phone': WHATSAPP_PHONE,
                'consent_type': 'tratamiento_datos',
                'accepted': True,
                'text_version': 'v1',
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {
            'consent_id': response.json()['consent_id'],
            'status': 'registered',
            'message': 'Consentimiento registrado.',
        })
        consent = WhatsAppInternalConsent.objects.get(id=response.json()['consent_id'])
        self.assertEqual(consent.product_type, 'whatsapp_credit')
        self.assertEqual(consent.document_number, WHATSAPP_DOCUMENT)
        audit = WhatsAppInternalAPIAuditLog.objects.filter(action='consent_create').first()
        self.assertIsNotNone(audit)
        self.assertNotEqual(audit.document_number_hash, '')
        self.assertEqual(audit.document_number_masked, '******4567')
        self.assertNotEqual(audit.document_number_hash, WHATSAPP_DOCUMENT)

    def test_application_audit_masks_document_and_does_not_store_payload(self):
        response = self._post_json('internal_whatsapp:applications', whatsapp_application_payload())

        self.assertEqual(response.status_code, 201)
        audit = WhatsAppInternalAPIAuditLog.objects.filter(action='application_create').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.document_number_masked, '******4567')
        self.assertNotEqual(audit.document_number_hash, WHATSAPP_DOCUMENT)
        self._assert_metadata_contains(audit.metadata, {
            'result': 'created',
            'application_id': response.json()['application_id'],
        })

    def test_application_validation_error_audit_masks_document_and_stores_safe_errors(self):
        response = self._post_json(
            'internal_whatsapp:applications',
            whatsapp_application_payload(monto_solicitado='2000000.01'),
        )

        self.assertEqual(response.status_code, 400)
        audit = WhatsAppInternalAPIAuditLog.objects.filter(action='application_create').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.document_number_masked, '******4567')
        self.assertNotEqual(audit.document_number_hash, WHATSAPP_DOCUMENT)
        self._assert_metadata_contains(audit.metadata, {
            'result': 'validation_error',
            'validation_errors': {'monto_solicitado': 'El monto maximo para whatsapp_credit es 2000000.'},
        })

    def test_structured_log_excludes_raw_pii_and_includes_observability_fields(self):
        payload = whatsapp_application_payload()

        with self.assertLogs('gestion_creditos.internal_whatsapp', level='INFO') as log_context:
            response = self.client.post(
                reverse('internal_whatsapp:applications'),
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_X_INTERNAL_API_KEY='test-internal-key',
                HTTP_X_REQUEST_ID='req-123',
                HTTP_X_CORRELATION_ID='corr-456',
                HTTP_X_IDEMPOTENCY_KEY='idem-sensitive-key',
            )

        self.assertEqual(response.status_code, 201)
        log_payload = json.loads(log_context.records[-1].getMessage())
        self.assertEqual(log_payload['request_id'], 'req-123')
        self.assertEqual(log_payload['correlation_id'], 'corr-456')
        self.assertEqual(log_payload['endpoint'], 'application_create')
        self.assertEqual(log_payload['method'], 'POST')
        self.assertEqual(log_payload['product_type'], 'whatsapp_credit')
        self.assertEqual(log_payload['status_code'], 201)
        self.assertIsInstance(log_payload['latency_ms'], int)
        self.assertEqual(log_payload['result'], 'created')
        self.assertEqual(log_payload['error_type'], '')
        self.assertIn('idempotency_key_hash', log_payload)

        raw_log = log_context.records[-1].getMessage()
        self.assertNotIn(WHATSAPP_DOCUMENT, raw_log)
        self.assertNotIn(WHATSAPP_PHONE, raw_log)
        self.assertNotIn(payload['correo'], raw_log)
        self.assertNotIn('idem-sensitive-key', raw_log)

        audit = WhatsAppInternalAPIAuditLog.objects.filter(action='application_create').first()
        self.assertEqual(audit.request_id, 'req-123')
        self.assertEqual(audit.correlation_id, 'corr-456')
        self.assertEqual(audit.metadata['correlation_id'], 'corr-456')
        self.assertEqual(audit.metadata['request_id'], 'req-123')
        self.assertIn('idempotency_key_hash', audit.metadata)

    def test_validation_error_log_contains_safe_error_fields_only(self):
        payload = whatsapp_application_payload(monto_solicitado='2000000.01')

        with self.assertLogs('gestion_creditos.internal_whatsapp', level='INFO') as log_context:
            response = self._post_json('internal_whatsapp:applications', payload)

        self.assertEqual(response.status_code, 400)
        log_payload = json.loads(log_context.records[-1].getMessage())
        self.assertEqual(log_payload['result'], 'validation_error')
        self.assertEqual(log_payload['error_type'], 'validation')
        self.assertEqual(log_payload['error_fields'], ['monto_solicitado'])

        raw_log = log_context.records[-1].getMessage()
        self.assertNotIn(WHATSAPP_DOCUMENT, raw_log)
        self.assertNotIn(WHATSAPP_PHONE, raw_log)
        self.assertNotIn(payload['correo'], raw_log)

    def test_internal_api_metrics_counters_are_recorded_per_endpoint(self):
        cache.clear()

        response = self._post_json('internal_whatsapp:applications', whatsapp_application_payload())

        self.assertEqual(response.status_code, 201)
        base_key = 'whatsapp-internal-api:metrics:application_create'
        self.assertEqual(cache.get(f'{base_key}:total'), 1)
        self.assertEqual(cache.get(f'{base_key}:2xx'), 1)
        self.assertEqual(cache.get(f'{base_key}:4xx') or 0, 0)
        self.assertEqual(cache.get(f'{base_key}:5xx') or 0, 0)
        self.assertEqual(cache.get(f'{base_key}:latency_ms_count'), 1)
        self.assertIsNotNone(cache.get(f'{base_key}:latency_ms_total'))
        self.assertIsNotNone(cache.get(f'{base_key}:latency_ms_last'))
