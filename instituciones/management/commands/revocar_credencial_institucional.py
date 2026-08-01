from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from instituciones.models import CredencialAPIInstitucion
from instituciones.services.credenciales import revocar_credencial_api


class Command(BaseCommand):
    help = 'Revoca una credencial institucional sin eliminar su trazabilidad.'

    def add_arguments(self, parser):
        parser.add_argument('--credencial-id', type=UUID, required=True)
        parser.add_argument('--confirmar', action='store_true')

    def handle(self, *args, **options):
        if not options['confirmar']:
            raise CommandError('La revocacion requiere --confirmar.')
        try:
            credencial = CredencialAPIInstitucion.objects.select_related(
                'institucion'
            ).get(pk=options['credencial_id'])
        except CredencialAPIInstitucion.DoesNotExist as exc:
            raise CommandError('La credencial indicada no existe.') from exc

        modificada = revocar_credencial_api(credencial=credencial)
        self.stdout.write(f'CREDENCIAL_ID={credencial.id}')
        self.stdout.write(f'PREFIJO={credencial.prefijo_clave}')
        self.stdout.write('ESTADO=REVOCADA' if modificada else 'ESTADO=YA_REVOCADA')
