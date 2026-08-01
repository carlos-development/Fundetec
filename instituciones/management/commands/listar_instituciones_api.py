from django.core.management.base import BaseCommand

from instituciones.models import Institucion


def _texto_seguro(valor):
    return ' '.join(str(valor or '').split())


class Command(BaseCommand):
    help = 'Lista instituciones y sus UUID sin mostrar credenciales ni secretos.'

    def add_arguments(self, parser):
        parser.add_argument('--solo-activas', action='store_true')

    def handle(self, *args, **options):
        instituciones = Institucion.objects.order_by('nombre_comercial', 'id')
        if options['solo_activas']:
            instituciones = instituciones.filter(activa=True)

        self.stdout.write('ID\tNOMBRE_COMERCIAL\tACTIVA')
        cantidad = 0
        for institucion in instituciones.iterator():
            self.stdout.write(
                f'{institucion.id}\t'
                f'{_texto_seguro(institucion.nombre_comercial)}\t'
                f'{str(institucion.activa).lower()}'
            )
            cantidad += 1
        self.stdout.write(f'TOTAL={cantidad}')
