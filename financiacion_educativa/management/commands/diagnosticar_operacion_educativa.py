import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from financiacion_educativa.services.diagnostico_operativo import (
    UMBRALES, alerta, comprobar_servicios, observar_base, validar_unidades,
)


class Command(BaseCommand):
    help = 'Alertas agregadas de solo lectura; no envia correos, firmas ni modifica pagos.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--servicio', action='append', default=[])
        for campo, valor in UMBRALES.items():
            parser.add_argument('--' + campo.replace('_', '-'), type=int, default=valor)

    def handle(self, *args, **opciones):
        if connection.in_atomic_block:
            raise CommandError('Ejecute fuera de una transaccion existente.', returncode=3)
        try:
            unidades = validar_unidades(opciones['servicio'])
            limites = {c: opciones[c] for c in UMBRALES}
            if any(not 1 <= v <= 10080 for v in limites.values()):
                raise ValueError('Umbrales fuera de rango.')
        except ValueError as exc:
            raise CommandError(str(exc), returncode=3) from None
        servicios, alertas = comprobar_servicios(unidades)
        try:
            with transaction.atomic():
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                datos = observar_base(umbrales=limites)
        except DatabaseError:
            datos = {'alertas': [alerta('DATABASE_DIAGNOSTIC_UNAVAILABLE', 1, 'CRITICAL',
                'Infraestructura de guardia', 'Revisar disponibilidad y permisos SELECT de PostgreSQL; no ejecutar restauracion ni escrituras.')],
                'diagnostico_incompleto': True}
        datos.update(solo_lectura=True, servicios=servicios)
        datos['alertas'].extend(alertas)
        datos['resultado'] = 'ALERT' if datos['alertas'] else 'OK'
        self.stdout.write(json.dumps(datos, sort_keys=True, default=str))
        if datos.get('diagnostico_incompleto'):
            raise CommandError('DIAGNOSTICO_INCOMPLETO; revisar JSON y journal.', returncode=3)
        if datos['alertas']:
            raise CommandError('ALERTAS_OPERATIVAS; revisar JSON y responsable.', returncode=2)
