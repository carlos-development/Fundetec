import json
from datetime import date
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from financiacion_educativa.dashboards.operaciones.cartera import (
    consultar_capital_pendiente, lectura_capital_al_corte, obligaciones_autorizadas,
)
from financiacion_educativa.models import CondicionesFinancieras


class Command(BaseCommand):
    help = 'Consulta de solo lectura del capital pendiente de una obligacion por fecha bancaria.'

    def add_arguments(self, parser):
        parser.add_argument('--obligacion-id', required=True, type=UUID, help='UUID de las condiciones contractuales.')
        parser.add_argument('--fecha-corte', required=True, type=date.fromisoformat, help='Fecha bancaria inclusiva (AAAA-MM-DD).')

    def handle(self, *args, obligacion_id, fecha_corte, **options):
        if connection.vendor != 'postgresql' or connection.in_atomic_block:
            raise CommandError('Ejecute la consulta en PostgreSQL fuera de una transaccion existente.')

        with lectura_capital_al_corte():
            consulta = obligaciones_autorizadas().filter(pk=obligacion_id)
            try:
                _, saldo = consultar_capital_pendiente(consulta, fecha_corte)
            except CondicionesFinancieras.DoesNotExist:
                raise CommandError('No hay una obligacion autorizada con ese UUID.') from None
        if saldo.get('libro_ingreso_id'):
            raise CommandError('La obligacion tiene movimientos de Libro 2B al corte. Consulte el libro ampliado.')
        if saldo.get('capital_inconsistente'):
            raise CommandError('El capital aplicado supera al capital contractual. Requiere revision.')
        self.stdout.write(json.dumps({
            'obligacion_id': str(obligacion_id),
            'fecha_corte': fecha_corte.isoformat(),
            'moneda': 'COP',
            'capital_financiado': format(saldo['capital_financiado'], '.2f'),
            'capital_aplicado': format(saldo['capital_aplicado'], '.2f'),
            'capital_pendiente': format(saldo['capital_pendiente'], '.2f'),
        }))
