from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.choices import (
    EstadoConfiguracionFinanciera,
    MetodoCalculoFinanciero,
    PoliticaCausacionInteres,
    PoliticaRedondeoFinanciero,
)
from financiacion_educativa.models import ConfiguracionFinancieraEducativa
from financiacion_educativa.services.configuracion_financiera import (
    CODIGO_POLITICA_ESTANDAR,
    activar_configuracion_financiera,
)


VALORES_INICIALES = {
    'porcentaje_originacion': Decimal('10'),
    'porcentaje_iva_originacion': Decimal('19'),
    'porcentaje_fondo_garantias': Decimal('2'),
    'proveedor_fondo_garantias': 'Figarantias',
    'porcentaje_seguro_vida': Decimal('0.3711'),
    'proveedor_seguro_vida': 'SURA',
    'tasa_interes_mensual': Decimal('1'),
    'moneda': 'COP',
    'metodo_calculo': MetodoCalculoFinanciero.FRENCH_AMORTIZATION,
    'politica_redondeo': PoliticaRedondeoFinanciero.COP_PESO_HALF_UP,
    'politica_causacion': PoliticaCausacionInteres.DAILY_30,
}


class Command(BaseCommand):
    help = 'Crea de forma idempotente la politica financiera educativa inicial.'

    def add_arguments(self, parser):
        parser.add_argument('--vigente-desde', required=True)
        parser.add_argument('--policy-version', type=int, default=1)
        parser.add_argument('--activate', action='store_true')

    def handle(self, *args, **options):
        try:
            vigente_desde = date.fromisoformat(options['vigente_desde'])
        except ValueError as exc:
            raise CommandError('Usa --vigente-desde en formato YYYY-MM-DD.') from exc
        version = options['policy_version']
        if version <= 0:
            raise CommandError('La version debe ser positiva.')

        configuracion, creada = ConfiguracionFinancieraEducativa.objects.get_or_create(
            codigo=CODIGO_POLITICA_ESTANDAR,
            version=version,
            defaults={
                'vigente_desde': vigente_desde,
                'estado': EstadoConfiguracionFinanciera.DRAFT,
                **VALORES_INICIALES,
            },
        )
        esperados = {'vigente_desde': vigente_desde, **VALORES_INICIALES}
        incompatibles = [
            campo
            for campo, esperado in esperados.items()
            if getattr(configuracion, campo) != esperado
        ]
        if incompatibles:
            raise CommandError(
                'La version existente tiene valores distintos: '
                + ', '.join(incompatibles)
            )
        if options['activate']:
            configuracion = activar_configuracion_financiera(
                configuracion=configuracion
            )
        accion = 'creada' if creada else 'existente'
        self.stdout.write(
            self.style.SUCCESS(
                f'Politica {configuracion} {accion}; estado={configuracion.estado}.'
            )
        )
