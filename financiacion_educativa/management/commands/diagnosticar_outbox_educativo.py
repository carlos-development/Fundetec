from django.core.management.base import BaseCommand

from financiacion_educativa.choices import EstadoOutboxCorreoEducativo
from financiacion_educativa.services.outbox_correos import conteos_outbox


class Command(BaseCommand):
    help = 'Muestra conteos no sensibles del outbox educativo.'

    def add_arguments(self, parser):
        parser.add_argument('--solicitud-id')
        parser.add_argument('--outbox-id')

    def handle(self, *args, **options):
        conteos = conteos_outbox(
            solicitud_id=options['solicitud_id'],
            outbox_id=options['outbox_id'],
        )
        for estado in EstadoOutboxCorreoEducativo.values:
            self.stdout.write(f'{estado}: {conteos.get(estado, 0)}')
