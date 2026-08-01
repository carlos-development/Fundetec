from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from instituciones.models import CredencialAPIInstitucion, Institucion


def _texto_seguro(valor):
    return ' '.join(str(valor or '').split())


def _fecha(valor):
    return valor.isoformat() if valor else '-'


class Command(BaseCommand):
    help = 'Lista metadatos de credenciales institucionales sin hashes ni secretos.'

    def add_arguments(self, parser):
        parser.add_argument('--institucion-id', type=UUID)
        parser.add_argument('--solo-activas', action='store_true')

    def handle(self, *args, **options):
        credenciales = (
            CredencialAPIInstitucion.objects.select_related('institucion')
            .defer('secreto_hash')
            .order_by('institucion__nombre_comercial', 'nombre', 'id')
        )
        institucion_id = options['institucion_id']
        if institucion_id:
            if not Institucion.objects.filter(pk=institucion_id).exists():
                raise CommandError('La institucion indicada no existe.')
            credenciales = credenciales.filter(institucion_id=institucion_id)
        if options['solo_activas']:
            credenciales = credenciales.filter(activa=True)

        self.stdout.write(
            'ID\tINSTITUCION_ID\tINSTITUCION\tNOMBRE\tPREFIJO\t'
            'ACTIVA\tVIGENTE\tEXPIRA_EN\tULTIMO_USO_EN'
        )
        cantidad = 0
        for credencial in credenciales.iterator():
            self.stdout.write(
                f'{credencial.id}\t{credencial.institucion_id}\t'
                f'{_texto_seguro(credencial.institucion.nombre_comercial)}\t'
                f'{_texto_seguro(credencial.nombre)}\t'
                f'{credencial.prefijo_clave}\t'
                f'{str(credencial.activa).lower()}\t'
                f'{str(credencial.vigente).lower()}\t'
                f'{_fecha(credencial.expira_en)}\t'
                f'{_fecha(credencial.ultimo_uso_en)}'
            )
            cantidad += 1
        self.stdout.write(f'TOTAL={cantidad}')
