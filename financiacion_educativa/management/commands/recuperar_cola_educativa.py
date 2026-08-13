from django.core.management.base import BaseCommand

from financiacion_educativa.services.cola_automatizacion import (
    recuperar_leases_vencidos,
)


class Command(BaseCommand):
    help = 'Recupera exclusivamente leases vencidos de la cola educativa.'

    def handle(self, *args, **options):
        recuperados = recuperar_leases_vencidos()
        self.stdout.write(f'Leases recuperados: {recuperados}.')
