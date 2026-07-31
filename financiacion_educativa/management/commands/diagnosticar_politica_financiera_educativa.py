import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from financiacion_educativa.models import ConfiguracionFinancieraEducativa
from financiacion_educativa.services.configuracion_financiera import (
    CODIGO_POLITICA_ESTANDAR,
    ConfiguracionFinancieraAmbigua,
    ConfiguracionFinancieraNoDisponible,
    seleccionar_configuracion_vigente,
)


def _identificador_base_datos():
    configuracion = connection.settings_dict
    if connection.vendor == 'sqlite':
        return str(Path(configuracion['NAME']).resolve())
    nombre = configuracion.get('NAME') or '(sin nombre)'
    host = configuracion.get('HOST') or '(host predeterminado)'
    return f'{host}/{nombre}'


class Command(BaseCommand):
    help = (
        'Diagnostica, sin modificar datos, la politica financiera educativa '
        'visible para este proceso.'
    )

    def handle(self, *args, **options):
        fecha = timezone.localdate()
        self.stdout.write(
            f'DJANGO_SETTINGS_MODULE='
            f'{os.environ.get("DJANGO_SETTINGS_MODULE", "(no definido)")}'
        )
        self.stdout.write(f'DATABASE_VENDOR={connection.vendor}')
        self.stdout.write(f'DATABASE_ENGINE={connection.settings_dict["ENGINE"]}')
        self.stdout.write(f'DATABASE_ID={_identificador_base_datos()}')
        self.stdout.write(f'TIME_ZONE={settings.TIME_ZONE}')
        self.stdout.write(f'LOCAL_DATE={fecha.isoformat()}')

        configuraciones = ConfiguracionFinancieraEducativa.objects.filter(
            codigo=CODIGO_POLITICA_ESTANDAR
        ).order_by('version')
        if not configuraciones:
            self.stdout.write('POLICIES=(ninguna)')
        for configuracion in configuraciones:
            self.stdout.write(
                'POLICY '
                f'codigo={configuracion.codigo} '
                f'version={configuracion.version} '
                f'estado={configuracion.estado} '
                f'desde={configuracion.vigente_desde.isoformat()} '
                f'hasta='
                f'{configuracion.vigente_hasta.isoformat() if configuracion.vigente_hasta else "(abierta)"}'
            )

        try:
            seleccionada = seleccionar_configuracion_vigente(
                fecha_aplicacion=fecha
            )
        except ConfiguracionFinancieraNoDisponible:
            self.stdout.write(self.style.ERROR('SELECTOR=NO_DISPONIBLE'))
        except ConfiguracionFinancieraAmbigua:
            self.stdout.write(self.style.ERROR('SELECTOR=AMBIGUA'))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'SELECTOR={seleccionada.codigo} v{seleccionada.version}'
                )
            )
