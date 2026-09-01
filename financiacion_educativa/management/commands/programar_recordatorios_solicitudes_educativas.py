from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.services.recordatorios_solicitudes import (
    programar_recordatorios_solicitudes,
)


class Command(BaseCommand):
    help = 'Programa recordatorios vencidos sin realizar entregas SMTP.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        limite = options['batch_size']
        if limite is not None and limite <= 0:
            raise CommandError('--batch-size debe ser positivo.')
        resultado = programar_recordatorios_solicitudes(
            limite=limite,
            dry_run=options['dry_run'],
        )
        self.stdout.write(
            'Evaluadas: {0.evaluadas}; programadas: {0.programadas}; '
            'omitidas: {0.omitidas}; errores: {0.errores}.'.format(resultado)
        )
