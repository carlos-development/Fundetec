from decimal import Decimal

from django.test import SimpleTestCase

from contractors.score.configuracion import CONFIGURACION_SCORE_PRESTADORES_V1
from contractors.score.dto import EntradaScoreInternoPrestador
from contractors.score.motor import evaluar_score_interno_prestador
from contractors.score.policies import (
    calcular_puntaje_capacidad_contractual,
    decimal_configuracion,
    obtener_bandas,
    validar_configuracion_score,
)


class ConfiguracionScoreInternoPrestadoresTests(SimpleTestCase):
    def test_pesos_suman_uno_excluyendo_geolocalizacion(self):
        self.assertTrue(validar_configuracion_score(CONFIGURACION_SCORE_PRESTADORES_V1))

    def test_bandas_cubren_rango_completo_sin_solaparse(self):
        bandas = sorted(obtener_bandas(CONFIGURACION_SCORE_PRESTADORES_V1), key=lambda banda: banda.minimo)

        self.assertEqual(bandas[0].minimo, Decimal('0'))
        self.assertEqual(bandas[-1].maximo, Decimal('1000'))
        for indice, banda in enumerate(bandas[:-1]):
            self.assertEqual(banda.maximo + Decimal('1'), bandas[indice + 1].minimo)

    def test_montos_y_plazos_salen_de_configuracion(self):
        banda_alta = next(
            banda for banda in obtener_bandas(CONFIGURACION_SCORE_PRESTADORES_V1) if banda.nombre == 'ALTA'
        )

        self.assertEqual(banda_alta.monto_maximo, Decimal('1500000.00'))
        self.assertEqual(banda_alta.plazo_maximo_meses, 6)


class MotorScoreInternoPrestadoresTests(SimpleTestCase):
    def _entrada(self, **componentes):
        return EntradaScoreInternoPrestador(
            solicitud_id=1,
            componentes=componentes,
            datacredito_status='PENDIENTE',
        )

    def test_score_premium_usando_configuracion(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=950,
                capacidad=930,
                comportamiento_digital=920,
                riesgo_fraude=910,
                referencias=900,
            )
        )

        self.assertEqual(resultado.banda.nombre, 'PREMIUM')
        self.assertEqual(resultado.decision_preliminar, 'APROBACION_DIRECTA_READ_ONLY')

    def test_score_alta(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=800,
                capacidad=820,
                comportamiento_digital=760,
                riesgo_fraude=760,
                referencias=760,
            )
        )

        self.assertEqual(resultado.banda.nombre, 'ALTA')

    def test_score_media(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=710,
                capacidad=700,
                comportamiento_digital=700,
                riesgo_fraude=700,
                referencias=700,
            )
        )

        self.assertEqual(resultado.banda.nombre, 'MEDIA')

    def test_score_entrada(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=640,
                capacidad=650,
                comportamiento_digital=650,
                riesgo_fraude=650,
                referencias=650,
            )
        )

        self.assertEqual(resultado.banda.nombre, 'ENTRADA')

    def test_score_revision(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=520,
                capacidad=520,
                comportamiento_digital=520,
                riesgo_fraude=520,
                referencias=520,
            )
        )

        self.assertEqual(resultado.banda.nombre, 'REVISION')
        self.assertTrue(resultado.requiere_revision_manual)

    def test_geolocalizacion_solo_penaliza(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=750,
                capacidad=750,
                comportamiento_digital=750,
                riesgo_fraude=750,
                referencias=750,
                geolocalizacion=500,
            )
        )

        self.assertEqual(resultado.score_final, Decimal('670.00'))
        self.assertEqual(resultado.banda.nombre, 'ENTRADA')
        self.assertEqual(resultado.penalizaciones[0].razon, 'geolocalizacion_bajo_umbral')

    def test_datacredito_pendiente_queda_marcado_y_score_es_parcial(self):
        resultado = evaluar_score_interno_prestador(self._entrada(capacidad=900))

        self.assertEqual(resultado.datacredito_status, 'PENDIENTE')
        self.assertIn('datacredito', resultado.componentes_pendientes)
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertEqual(resultado.fuente, 'score_interno_read_only')

    def test_componentes_default_funcionan(self):
        resultado = evaluar_score_interno_prestador(self._entrada(capacidad=900))
        componentes = {componente.nombre: componente for componente in resultado.componentes}

        self.assertEqual(componentes['comportamiento_digital'].valor, Decimal('750.00'))
        self.assertEqual(componentes['riesgo_fraude'].valor, Decimal('750.00'))
        self.assertEqual(componentes['referencias'].valor, Decimal('750.00'))

    def test_valores_fuera_de_rango_se_normalizan(self):
        resultado = evaluar_score_interno_prestador(
            self._entrada(
                datacredito=1300,
                capacidad=1300,
                comportamiento_digital=1300,
                riesgo_fraude=1300,
                referencias=1300,
            )
        )

        self.assertEqual(resultado.score_final, Decimal('1000.00'))
        self.assertEqual(resultado.banda.nombre, 'PREMIUM')

    def test_capacidad_contractual_se_convierte_a_puntaje_desde_configuracion(self):
        puntaje = calcular_puntaje_capacidad_contractual(
            {
                'monto_solicitado': Decimal('1000000.00'),
                'capacidad_maxima_estimada': Decimal('8000000.00'),
            },
            CONFIGURACION_SCORE_PRESTADORES_V1,
        )

        self.assertEqual(puntaje, Decimal('900.00'))

    def test_decimal_configuracion_no_usa_float(self):
        self.assertEqual(decimal_configuracion('0.25'), Decimal('0.25'))
