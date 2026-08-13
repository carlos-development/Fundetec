from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.services.outbox_correos import (
    recuperar_leases_outbox,
    reintentar_fallidos,
    resolver_ambiguos,
)


class Command(BaseCommand):
    help = 'Recupera o concilia el outbox educativo de forma explicita.'

    def add_arguments(self, parser):
        acciones = parser.add_mutually_exclusive_group(required=True)
        acciones.add_argument('--recover-leases', action='store_true')
        acciones.add_argument('--retry-failed', action='store_true')
        acciones.add_argument(
            '--resolve-ambiguous',
            choices=['SENT', 'FAILED', 'RETRYING'],
        )
        parser.add_argument('--solicitud-id')
        parser.add_argument('--outbox-id')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--confirmar', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if not dry_run and not options['confirmar']:
            raise CommandError(
                'La operacion requiere --confirmar o debe ejecutarse con --dry-run.'
            )
        filtros = {
            'dry_run': dry_run,
            'solicitud_id': options['solicitud_id'],
            'outbox_id': options['outbox_id'],
        }
        if options['recover_leases']:
            total = recuperar_leases_outbox(**filtros)
            accion = 'leases vencidos'
        elif options['retry_failed']:
            total = reintentar_fallidos(**filtros)
            accion = 'fallidos'
        else:
            total = resolver_ambiguos(
                resolucion=options['resolve_ambiguous'],
                **filtros,
            )
            accion = 'ambiguos'
        modo = 'dry-run' if dry_run else 'aplicado'
        self.stdout.write(f'{accion}: {total}; modo={modo}.')
