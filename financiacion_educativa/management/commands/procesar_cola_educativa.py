from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.services.cola_automatizacion import ejecutar_worker


class Command(BaseCommand):
    help = 'Procesa la cola PostgreSQL de automatizacion educativa.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--poll-seconds', type=int, default=2)

    def handle(self, *args, **options):
        if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
            raise CommandError('La automatizacion educativa esta deshabilitada.')
        limite = options['limit']
        intervalo = options['poll_seconds']
        if limite is not None and limite <= 0:
            raise CommandError('--limit debe ser positivo.')
        if intervalo < 1 or intervalo > 60:
            raise CommandError('--poll-seconds debe estar entre 1 y 60.')
        procesados = ejecutar_worker(
            limite=limite,
            intervalo=intervalo,
            una_vez=options['once'],
        )
        self.stdout.write(f'Procesos ejecutados: {procesados}.')
