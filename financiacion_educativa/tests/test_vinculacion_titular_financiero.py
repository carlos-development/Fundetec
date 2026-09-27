import json
from dataclasses import replace
from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from financiacion_educativa.services.clasificacion_contenido_documental import (
    _aplicar_consistencia_determinista, _campos_persistibles, decidir_politica_contenido,
)
from financiacion_educativa.tests.content_validation_backends import resultado_concluyente


@override_settings(
    FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE='0.85',
    FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS='0.80',
    FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY='0.80',
)
class VinculacionTitularFinancieroTests(SimpleTestCase):
    contexto = {
        'holder_name': 'ANA MARIA PEREZ LOPEZ', 'holder_document_number': '10000001',
        'institution_name': 'INSTITUCION SINTETICA', 'program_name': 'CURSO SINTETICO',
    }

    def resultado(self, *, campos=None, **cambios):
        base = resultado_concluyente(tipo_esperado='INCOME_CERTIFICATE', contexto=self.contexto)
        return replace(base, campos_extraidos={**base.campos_extraidos, **(campos or {})}, **cambios)

    def evaluar(self, resultado):
        return decidir_politica_contenido(resultado, tipo='INCOME_CERTIFICATE', contexto=self.contexto)

    def test_misma_extraccion_con_ia_alternando_no_cambia_decision(self):
        for sugerencia in ('MATCH', 'MISMATCH', 'INCONCLUSIVE'):
            with self.subTest(sugerencia=sugerencia):
                resultado = self.resultado(
                    coincidencia_titular=sugerencia, codigos_razon=('DATA_MISMATCH',),
                    resultado_general='CORRECTION_REQUIRED',
                    campos={'holder_document_number': '10.000.001', 'holder_name': 'NOMBRE INCOMPLETO'},
                )
                self.assertEqual(self.evaluar(resultado), ('ACCEPTED', ['ACCEPTED']))
                self.assertEqual(resultado.coincidencia_titular, sugerencia)

    def test_nombre_completo_sin_numero_toleras_tildes_orden_no_parciales(self):
        for nombre, esperado in (
            ('  LÓPEZ pérez María ANA ', 'ACCEPTED'),
            ('ANA', 'MANUAL_EXCEPTION'), ('ANA MARIA', 'MANUAL_EXCEPTION'),
            ('ANA MARIA PEREZ LOPEZ OTRA', 'MANUAL_EXCEPTION'), ('', 'MANUAL_EXCEPTION'),
        ):
            with self.subTest(nombre=nombre):
                self.assertEqual(self.evaluar(self.resultado(campos={
                    'holder_name': nombre, 'holder_document_number': '',
                }))[0], esperado)

    def test_numero_contradictorio_no_es_superado_por_match_ia(self):
        self.assertEqual(self.evaluar(self.resultado(campos={
            'holder_document_number': '20000002',
        })), ('CORRECTION_REQUIRED', ['DATA_MISMATCH']))

    def test_extraccion_incierta_no_obliga_correccion(self):
        resultado = self.resultado(
            campos={'holder_document_number': '20000002'},
            completitud_extraccion=Decimal('0.4'), coincidencia_titular='MISMATCH',
        )
        self.assertEqual(self.evaluar(resultado), ('MANUAL_EXCEPTION', ['INCONCLUSIVE']))
        for propuesta in ('MATCH', 'MISMATCH'):
            self.assertEqual(decidir_politica_contenido(
                self.resultado(coincidencia_titular=propuesta), tipo='INCOME_CERTIFICATE',
            )[0], 'MANUAL_EXCEPTION')

    def test_traza_solo_senales_y_version_sin_mutar_resultado_original(self):
        original = self.resultado(coincidencia_titular='MISMATCH')
        nuevo = _aplicar_consistencia_determinista(original, contexto=self.contexto, tipo='INCOME_CERTIFICATE')
        traza = _campos_persistibles(nuevo)
        self.assertEqual(traza['holder_policy_version'], 'EDU_FINANCIAL_HOLDER_V1')
        self.assertEqual(traza['holder_link_method'], 'EXACT_DOCUMENT_NUMBER')
        self.assertEqual(original.vinculo_titular, {})
        self.assertEqual(original.coincidencia_titular, 'MISMATCH')
        for sensible in ('ANA', '10000001', 'holder_name', 'holder_document_number'):
            self.assertNotIn(sensible, json.dumps(traza))
