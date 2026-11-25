import time
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.choices import EstadoOutboxCorreoEducativo
from financiacion_educativa.services.outbox_correos import (
    procesar_siguiente_correo,
)


class Command(BaseCommand):
    help = 'Procesa el outbox persistente de correo educativo.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--interval', type=float, default=2.0)
        parser.add_argument(
            '--aviso-plan-id', type=UUID,
            help='Despacha exclusivamente el aviso indicado si el envio de avisos de plan esta habilitado.',
        )

    def handle(self, *args, **options):
        limite = options['limit']
        aviso_plan_id = options['aviso_plan_id']
        if aviso_plan_id is not None:
            if options['once'] or limite is not None:
                raise CommandError('--aviso-plan-id se usa sin --once ni --limit.')
            resultado = procesar_siguiente_correo(aviso_plan_id=aviso_plan_id)
            if not resultado.procesado:
                self.stdout.write('El aviso indicado no esta disponible para despacho.')
            elif resultado.estado != EstadoOutboxCorreoEducativo.SENT:
                raise CommandError(f'Estado del aviso: {resultado.estado} ({resultado.codigo}).')
            else:
                self.stdout.write('Aviso de plan enviado.')
            return
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
