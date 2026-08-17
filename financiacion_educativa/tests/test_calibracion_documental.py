import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from financiacion_educativa.choices import CategoriaContenidoDocumento
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    DecisionRevisionEducativa,
    EventoWebhookFirmaEducativa,
    ParticipanteFinanciacion,
    ProcesoFirmaEducativa,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.calibracion_documental import (
    MANIFEST_VERSION,
    PRIVATE_CONTEXT_VERSION,
)
from financiacion_educativa.services.metricas_openai import extraer_metricas_uso
from financiacion_educativa.services.validacion_documental_ia import (
    IDENTITY_POLICY_VERSION,
)
from financiacion_educativa.tests.calibration_backends import (
    CalibrationContentConclusiveBackend,
    CalibrationIdentityConclusiveBackend,
    CalibrationIdentityMalformedBackend,
    CalibrationIdentityPermanentBackend,
    CalibrationIdentityTemporaryBackend,
)
from financiacion_educativa.tests.factories import imagen_jpeg_prueba


IDENTITY_BACKEND = (
    'financiacion_educativa.tests.calibration_backends.'
    'CalibrationIdentityConclusiveBackend'
)
CONTENT_BACKEND = (
    'financiacion_educativa.tests.calibration_backends.'
    'CalibrationContentConclusiveBackend'
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_CALIBRATION_ALLOW_TEST_BACKENDS=True,
    FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND=IDENTITY_BACKEND,
    FINANCIACION_EDUCATIVA_CALIBRATION_CONTENT_BACKEND=CONTENT_BACKEND,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS=3,
    FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS=3,
)
class CalibracionDocumentalCommandTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.dataset = self.root / 'private-dataset'
        self.reports = self.root / 'private-reports'
        self.dataset.mkdir()
        self.reports.mkdir()
        self.manifest = self.root / 'manifest.json'
        self.output = self.reports / 'report.json'
        mail.outbox.clear()
        for backend in (
            CalibrationIdentityConclusiveBackend,
            CalibrationIdentityTemporaryBackend,
            CalibrationIdentityPermanentBackend,
            CalibrationIdentityMalformedBackend,
            CalibrationContentConclusiveBackend,
        ):
            backend.reset()

    def tearDown(self):
        self.temporary.cleanup()

    def _identity_case(self, case_id='CASE_ID_FRONT_001', **overrides):
        values = {
            'case_id': case_id,
            'relative_path': f'{case_id.lower()}.jpg',
            'expected_document_type': 'STUDENT_ID_FRONT',
            'expected_side': 'FRONT',
            'expected_outcome': 'ACCEPT',
            'expected_reasons': [],
            'format': 'JPEG',
            'document_category': 'IDENTITY',
            'holder_alias': 'ALIAS_HOLDER_001',
            'notes': 'Muestra sintetica autorizada',
        }
        values.update(overrides)
        return values

    def _content_case(self, case_id='CASE_INCOME_001', **overrides):
        values = {
            'case_id': case_id,
            'relative_path': f'{case_id.lower()}.jpg',
            'expected_document_type': 'INCOME_CERTIFICATE',
            'expected_side': 'NOT_APPLICABLE',
            'expected_outcome': 'ACCEPT',
            'expected_reasons': [],
            'format': 'JPEG',
            'document_category': CategoriaContenidoDocumento.INCOME_CERTIFICATE,
            'holder_alias': 'ALIAS_HOLDER_001',
            'notes': 'Muestra sintetica autorizada',
        }
        values.update(overrides)
        return values

    def _write_image(self, case):
        upload = imagen_jpeg_prueba(case['relative_path'], case['case_id'])
        path = self.dataset / case['relative_path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(upload.read())
        return path

    def _write_manifest(self, cases, *, payload=None):
        data = payload or {
            'schema_version': MANIFEST_VERSION,
            'cases': cases,
        }
        self.manifest.write_text(json.dumps(data), encoding='utf-8')

    def _run(self, *, execute=False, **options):
        stdout = StringIO()
        call_command(
            'calibrar_documentos_educativos',
            dataset=str(options.pop('dataset', self.dataset)),
            manifest=str(options.pop('manifest', self.manifest)),
            output=str(options.pop('output', self.output)),
            execute=execute,
            stdout=stdout,
            **options,
        )
        return json.loads(self.output.read_text(encoding='utf-8')), stdout.getvalue()

    def test_dry_run_is_default_and_never_calls_provider(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])

        report, output = self._run()

        self.assertTrue(report['dry_run'])
        self.assertEqual(report['cases'][0]['provider_result'], 'NOT_CALLED')
        self.assertEqual(CalibrationIdentityConclusiveBackend.calls, 0)
        self.assertIn('dry_run=true', output)

    @override_settings(
        FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND=(
            'financiacion_educativa.services.validacion_documental_ia.'
            'OpenAIDocumentAIValidationBackend'
        ),
        FINANCIACION_EDUCATIVA_CALIBRATION_OPENAI_ENABLED=False,
        OPENAI_API_KEY='synthetic-test-key',
    )
    def test_real_openai_requires_both_explicit_authorizations(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])

        with patch(
            'financiacion_educativa.services.validacion_documental_ia.'
            'OpenAIDocumentAIValidationBackend.validar'
        ) as provider:
            with self.assertRaisesMessage(CommandError, 'REAL_OPENAI_NOT_AUTHORIZED'):
                self._run(execute=True, allow_real_openai=True)

        provider.assert_not_called()

    def test_rejects_repository_and_public_paths(self):
        case = self._identity_case()
        self._write_manifest([case])

        with self.assertRaisesMessage(CommandError, 'PROHIBITED_PATH'):
            self._run(dataset=Path(settings.BASE_DIR))

        public_dataset = self.root / 'media' / 'dataset'
        public_dataset.mkdir(parents=True)
        with self.assertRaisesMessage(CommandError, 'PROHIBITED_PATH'):
            self._run(dataset=public_dataset)

    @override_settings(
        FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND=(
            'financiacion_educativa.services.validacion_documental_ia.'
            'OpenAIDocumentAIValidationBackend'
        ),
        FINANCIACION_EDUCATIVA_CALIBRATION_CONTENT_BACKEND=(
            'financiacion_educativa.services.clasificacion_contenido_documental.'
            'OpenAIContentDocumentClassificationBackend'
        ),
        FINANCIACION_EDUCATIVA_CALIBRATION_OPENAI_ENABLED=True,
        OPENAI_API_KEY='synthetic-test-key',
    )
    def test_real_execution_requires_exact_private_context_case_set(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])
        private_context = self.root / 'private-context.json'
        private_context.write_text(json.dumps({
            'schema_version': PRIVATE_CONTEXT_VERSION,
            'cases': {},
        }), encoding='utf-8')

        with patch(
            'financiacion_educativa.services.validacion_documental_ia.'
            'OpenAIDocumentAIValidationBackend.validar'
        ) as identity_provider, patch(
            'financiacion_educativa.services.clasificacion_contenido_documental.'
            'OpenAIContentDocumentClassificationBackend.clasificar'
        ) as content_provider:
            with self.assertRaisesMessage(
                CommandError,
                'PRIVATE_CONTEXT_CASES_MISMATCH',
            ):
                self._run(
                    execute=True,
                    allow_real_openai=True,
                    private_context=str(private_context),
                )

        identity_provider.assert_not_called()
        content_provider.assert_not_called()

    def test_rejects_invalid_manifest_schema(self):
        self._write_manifest([], payload={'schema_version': MANIFEST_VERSION})

        with self.assertRaisesMessage(CommandError, 'INVALID_MANIFEST'):
            self._run()

    def test_rejects_duplicate_case_ids(self):
        case = self._identity_case()
        self._write_manifest([case, dict(case)])

        with self.assertRaisesMessage(CommandError, 'DUPLICATE_CASE_ID'):
            self._run()

    def test_missing_file_isolated_and_following_case_continues(self):
        missing = self._identity_case('CASE_ID_FRONT_MISSING')
        valid = self._identity_case('CASE_ID_FRONT_VALID')
        self._write_image(valid)
        self._write_manifest([missing, valid])

        report, _ = self._run(execute=True)

        self.assertEqual(report['cases'][0]['technical_error']['code'], 'FILE_NOT_FOUND')
        self.assertEqual(report['cases'][1]['deterministic_outcome'], 'ACCEPT')
        self.assertEqual(CalibrationIdentityConclusiveBackend.calls, 1)

    def test_rejects_disallowed_or_mismatched_extension(self):
        case = self._identity_case(format='PNG')
        self._write_manifest([case])

        with self.assertRaisesMessage(CommandError, 'FORMAT_EXTENSION_MISMATCH'):
            self._run()

    def test_report_excludes_manifest_notes_alias_and_private_pii(self):
        case = self._identity_case(notes='Etiqueta interna no sensible')
        self._write_image(case)
        self._write_manifest([case])
        private_context = self.root / 'private-context.json'
        private_context.write_text(json.dumps({
            'schema_version': PRIVATE_CONTEXT_VERSION,
            'cases': {
                case['case_id']: {
                    'holder_name': 'PRIVATE TEST HOLDER',
                    'holder_document_number': '987654321',
                    'document_type': 'CC',
                    'birth_date': '2000-01-01',
                },
            },
        }), encoding='utf-8')

        self._run(execute=True, private_context=str(private_context))
        raw_report = self.output.read_text(encoding='utf-8')

        self.assertNotIn('PRIVATE TEST HOLDER', raw_report)
        self.assertNotIn('987654321', raw_report)
        self.assertNotIn(case['holder_alias'], raw_report)
        self.assertNotIn(case['notes'], raw_report)
        self.assertNotIn(case['relative_path'], raw_report)

    @override_settings(
        FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND=(
            'financiacion_educativa.tests.calibration_backends.'
            'CalibrationIdentityTemporaryBackend'
        ),
    )
    def test_temporary_error_retries_and_is_not_document_metric(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])

        report, _ = self._run(execute=True)
        result = report['cases'][0]

        self.assertEqual(CalibrationIdentityTemporaryBackend.calls, 3)
        self.assertEqual(result['retries'], 2)
        self.assertEqual(result['technical_error']['classification'], 'TEMPORARY')
        self.assertEqual(report['metrics']['evaluated_cases'], 0)
        self.assertEqual(report['metrics']['technical_errors'], 1)

    @override_settings(
        FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND=(
            'financiacion_educativa.tests.calibration_backends.'
            'CalibrationIdentityPermanentBackend'
        ),
    )
    def test_permanent_error_does_not_retry(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])

        report, _ = self._run(execute=True)

        self.assertEqual(CalibrationIdentityPermanentBackend.calls, 1)
        self.assertEqual(report['cases'][0]['retries'], 0)
        self.assertEqual(
            report['cases'][0]['technical_error']['classification'],
            'PERMANENT',
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND=(
            'financiacion_educativa.tests.calibration_backends.'
            'CalibrationIdentityMalformedBackend'
        ),
    )
    def test_malformed_provider_response_is_sanitized(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])

        report, _ = self._run(execute=True)
        result = report['cases'][0]

        self.assertEqual(CalibrationIdentityMalformedBackend.calls, 1)
        self.assertEqual(result['schema_validation'], 'INVALID')
        self.assertEqual(result['technical_error']['code'], 'INVALID_RESPONSE')
        self.assertNotIn('unexpected', json.dumps(report))

    def test_metrics_and_critical_false_accept_are_reported(self):
        accepted = self._identity_case('CASE_TRUE_ACCEPT')
        false_accept = self._identity_case(
            'CASE_FALSE_ACCEPT',
            expected_outcome='CORRECTION',
            expected_reasons=['TYPE_MISMATCH'],
        )
        self._write_image(accepted)
        self._write_image(false_accept)
        self._write_manifest([accepted, false_accept])

        report, _ = self._run(execute=True)

        self.assertEqual(report['metrics']['true_accepts'], 1)
        self.assertEqual(report['metrics']['false_accepts'], 1)
        self.assertTrue(report['cases'][1]['critical_false_accept'])
        self.assertEqual(
            report['policy_proposal']['status'],
            'BLOCKED_BY_FALSE_ACCEPTS',
        )
        self.assertEqual(
            report['cases'][0]['provider_usage']['total_tokens'],
            120,
        )
        self.assertEqual(
            report['configuration']['identity_policy_version'],
            IDENTITY_POLICY_VERSION,
        )
        self.assertEqual(
            report['configuration']['identity_policy_version'],
            'EDU_IDENTITY_V3',
        )

    def test_content_case_uses_content_policy_and_usage_metrics(self):
        case = self._content_case()
        self._write_image(case)
        self._write_manifest([case])

        report, _ = self._run(execute=True)
        result = report['cases'][0]

        self.assertEqual(result['deterministic_outcome'], 'ACCEPT')
        self.assertEqual(result['schema_validation'], 'VALID')
        self.assertEqual(result['provider_usage']['total_tokens'], 230)
        self.assertEqual(CalibrationContentConclusiveBackend.calls, 1)

    def test_usage_extraction_only_keeps_non_sensitive_integer_counters(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            input_tokens=123,
            output_tokens=45,
            total_tokens=168,
            raw_details={'prompt': 'must-not-be-copied'},
        ))

        self.assertEqual(extraer_metricas_uso(response), {
            'input_tokens': 123,
            'output_tokens': 45,
            'total_tokens': 168,
        })

    def test_command_has_no_contractual_or_external_side_effects(self):
        case = self._identity_case()
        self._write_image(case)
        self._write_manifest([case])
        models = (
            SolicitudFinanciacionEducativa,
            ParticipanteFinanciacion,
            DecisionRevisionEducativa,
            CondicionesFinancieras,
            ArtefactoContractualEducativo,
            ProcesoFirmaEducativa,
            EventoWebhookFirmaEducativa,
        )
        before = {model: model.objects.count() for model in models}

        with patch(
            'django.core.mail.backends.locmem.EmailBackend.send_messages'
        ) as smtp, patch(
            'financiacion_educativa.services.firma_zapsign.'
            'enviar_pagare_educativo'
        ) as zapsign, patch(
            'financiacion_educativa.services.firma_zapsign.'
            'procesar_webhook_firma'
        ) as webhook:
            report, _ = self._run(execute=True)

        self.assertEqual(
            {model: model.objects.count() for model in models},
            before,
        )
        self.assertEqual(len(mail.outbox), 0)
        smtp.assert_not_called()
        zapsign.assert_not_called()
        webhook.assert_not_called()
        self.assertFalse(any(self.reports.glob('*.jpg')))
        self.assertFalse(any(self.reports.glob('*.pdf')))
        self.assertEqual(report['cases'][0]['deterministic_outcome'], 'ACCEPT')
