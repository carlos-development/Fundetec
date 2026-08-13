from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from financiacion_educativa.choices import EstadoProcesoAutomatizacionEducativa
from financiacion_educativa.models import ProcesoAutomatizacionEducativa


class Command(BaseCommand):
    help = 'Muestra un diagnostico no sensible de la cola educativa.'

    def handle(self, *args, **options):
        ahora = timezone.now()
        conteos = {
            fila['estado']: fila['total']
            for fila in ProcesoAutomatizacionEducativa.objects.values('estado')
            .annotate(total=Count('id'))
            .order_by('estado')
        }
        vencidos = ProcesoAutomatizacionEducativa.objects.filter(
            estado=EstadoProcesoAutomatizacionEducativa.RUNNING,
            lease_vence_en__lte=ahora,
        ).count()
        for estado in EstadoProcesoAutomatizacionEducativa.values:
            self.stdout.write(f'{estado}: {conteos.get(estado, 0)}')
        self.stdout.write(f'LEASES_EXPIRED: {vencidos}')
