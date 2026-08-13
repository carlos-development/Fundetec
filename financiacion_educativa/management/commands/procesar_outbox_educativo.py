import time

from django.core.management.base import BaseCommand

from financiacion_educativa.services.outbox_correos import (
    procesar_siguiente_correo,
)


class Command(BaseCommand):
    help = 'Procesa el outbox persistente de correo educativo.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--interval', type=float, default=2.0)

    def handle(self, *args, **options):
        limite = options['limit']
        if limite is not None and limite <= 0:
            self.stderr.write('El limite debe ser positivo.')
            return
        procesados = 0
        while limite is None or procesados < limite:
            resultado = procesar_siguiente_correo()
            if resultado.procesado:
                procesados += 1
                continue
            if options['once'] or limite is not None:
                break
            time.sleep(max(0.1, options['interval']))
        self.stdout.write(f'Correos procesados: {procesados}.')
