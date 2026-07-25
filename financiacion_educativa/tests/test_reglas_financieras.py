from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from financiacion_educativa.choices import EstadoConfiguracionFinanciera
from financiacion_educativa.models import (
    CondicionesFinancieras,
    ConfiguracionFinancieraEducativa,
)
from financiacion_educativa.services.configuracion_financiera import (
    activar_configuracion_financiera,
    seleccionar_configuracion_vigente,
)
from financiacion_educativa.services.motor_financiero import calcular_cargos
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)


FECHA_CALCULO = timezone.make_aware(datetime(2026, 7, 25, 12, 0))


class ConfiguracionYFotografiaFinancieraTests(TestCase):
    def setUp(self):
        self.configuracion = crear_configuracion_financiera()
        self.solicitud = crear_solicitud()
        self.solicitud.plazo_meses = 3
        self.solicitud.save(update_fields=['plazo_meses'])

    def _crear_fotografia(self, **kwargs):
        return crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 7, 31),
            fecha_calculo=FECHA_CALCULO,
            **kwargs,
        )

    def test_cargos_confirmados_para_un_millon(self):
        resultado = calcular_cargos(
            monto_solicitado=Decimal('1000000'),
            porcentaje_originacion=Decimal('10'),
            porcentaje_iva_originacion=Decimal('19'),
            porcentaje_fondo_garantias=Decimal('2'),
            porcentaje_seguro_vida=Decimal('0.3711'),
        )

        self.assertEqual(resultado.valor_originacion, Decimal('100000'))
        self.assertEqual(resultado.valor_iva_originacion, Decimal('19000'))
        self.assertEqual(resultado.valor_fondo_garantias, Decimal('20000'))
        self.assertEqual(resultado.valor_seguro_vida, Decimal('3711'))
        self.assertEqual(resultado.capital_total_financiado, Decimal('1142711'))

    def test_rechaza_float_y_valores_no_positivos(self):
        with self.assertRaises(ValidationError):
            calcular_cargos(
                monto_solicitado=1000000.0,
                porcentaje_originacion=Decimal('10'),
                porcentaje_iva_originacion=Decimal('19'),
                porcentaje_fondo_garantias=Decimal('2'),
                porcentaje_seguro_vida=Decimal('0.3711'),
            )
        with self.assertRaises(ValidationError):
            calcular_cargos(
                monto_solicitado=Decimal('0'),
                porcentaje_originacion=Decimal('10'),
                porcentaje_iva_originacion=Decimal('19'),
                porcentaje_fondo_garantias=Decimal('2'),
                porcentaje_seguro_vida=Decimal('0.3711'),
            )

    def test_caso_aceptacion_produce_resultado_matematico_exacto(self):
        fotografia = self._crear_fotografia()

        self.assertEqual(fotografia.capital_financiado, Decimal('1142711'))
        self.assertEqual(fotografia.valor_cuota_estimada, Decimal('388547'))
        self.assertEqual(fotografia.interes_total_estimado, Decimal('22930'))
        self.assertEqual(fotografia.total_estimado, Decimal('1165641'))
        self.assertEqual(fotografia.cuotas.count(), 3)
        self.assertEqual(fotografia.cuotas.last().saldo_final, Decimal('0'))
        self.assertEqual(
            sum(fotografia.cuotas.values_list('capital', flat=True)),
            fotografia.capital_financiado,
        )

    def test_fotografia_conserva_parametros_y_proveedores(self):
        fotografia = self._crear_fotografia()

        self.assertEqual(fotografia.configuracion, self.configuracion)
        self.assertEqual(fotografia.tasa_interes_mensual, Decimal('1'))
        self.assertEqual(fotografia.tasa_fondo_garantias, Decimal('2'))
        self.assertEqual(fotografia.proveedor_fondo_garantias, 'Figarantias')
        self.assertEqual(fotografia.tasa_seguro_vida, Decimal('0.3711'))
        self.assertEqual(fotografia.proveedor_seguro_vida, 'SURA')
        self.assertEqual(len(fotografia.huella_determinantes), 64)

    def test_reintento_idempotente_no_crea_otra_version(self):
        primera = self._crear_fotografia()
        segunda = self._crear_fotografia()

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(CondicionesFinancieras.objects.count(), 1)

    def test_nueva_configuracion_crea_version_y_desactiva_anterior(self):
        anterior = self._crear_fotografia()
        ConfiguracionFinancieraEducativa.objects.filter(
            pk=self.configuracion.pk
        ).update(estado=EstadoConfiguracionFinanciera.RETIRED)
        nueva = crear_configuracion_financiera(
            version=2,
            vigente_desde=date(2026, 7, 25),
            tasa_interes=Decimal('0'),
        )

        actual = self._crear_fotografia(configuracion=nueva)
        anterior.refresh_from_db()

        self.assertFalse(anterior.activa)
        self.assertTrue(actual.activa)
        self.assertEqual(actual.numero_version, 2)
        self.assertEqual(anterior.valor_cuota_estimada, Decimal('388547'))
        self.assertEqual(CondicionesFinancieras.objects.filter(activa=True).count(), 1)

    def test_fotografia_bloqueada_no_se_recalcula(self):
        self._crear_fotografia(bloquear=True)
        nueva = crear_configuracion_financiera(
            version=2,
            vigente_desde=date(2026, 7, 25),
            tasa_interes=Decimal('0'),
        )

        with self.assertRaises(ValidationError):
            self._crear_fotografia(configuracion=nueva)

    def test_selecciona_vigencia_correcta_y_rechaza_superposicion(self):
        self.assertEqual(
            seleccionar_configuracion_vigente(
                fecha_aplicacion=date(2026, 7, 25)
            ),
            self.configuracion,
        )
        borrador = crear_configuracion_financiera(
            version=2,
            vigente_desde=date(2026, 6, 1),
            estado=EstadoConfiguracionFinanciera.DRAFT,
        )
        with self.assertRaises(ValidationError):
            activar_configuracion_financiera(configuracion=borrador)

    def test_configuracion_aplicada_no_puede_modificarse(self):
        self._crear_fotografia()
        self.configuracion.tasa_interes_mensual = Decimal('2')
        with self.assertRaises(ValidationError):
            self.configuracion.save()

    def test_comando_inicial_es_idempotente(self):
        ConfiguracionFinancieraEducativa.objects.all().delete()
        salida = StringIO()
        call_command(
            'configurar_politica_financiera_educativa',
            vigente_desde='2026-01-01',
            activate=True,
            stdout=salida,
        )
        call_command(
            'configurar_politica_financiera_educativa',
            vigente_desde='2026-01-01',
            activate=True,
            stdout=salida,
        )

        self.assertEqual(ConfiguracionFinancieraEducativa.objects.count(), 1)
        config = ConfiguracionFinancieraEducativa.objects.get()
        self.assertEqual(config.estado, EstadoConfiguracionFinanciera.ACTIVE)
        self.assertEqual(config.porcentaje_seguro_vida, Decimal('0.371100'))
