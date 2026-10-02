from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.cancelacion_sandbox import (
    cancelar_solicitud_sandbox,
    validar_ambiente_cancelacion_sandbox,
)


class Command(BaseCommand):
    help = 'Cancela una solicitud de staging/test preservando toda su auditoria.'

    def add_arguments(self, parser):
        parser.add_argument('--application-id', required=True, type=UUID)
        parser.add_argument('--actor-id', required=True, type=int)
        parser.add_argument('--confirm-reference', required=True)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--confirm', action='store_true')

    def handle(self, *args, **options):
        try:
            validar_ambiente_cancelacion_sandbox()
            if not options['confirm']:
                raise CommandError('Se requiere --confirm.')
            solicitud = SolicitudFinanciacionEducativa.objects.only(
                'referencia_externa'
            ).get(pk=options['application_id'])
            if options['confirm_reference'] != solicitud.referencia_externa:
                raise CommandError(
                    '--confirm-reference no coincide exactamente con la solicitud.'
                )
            actor = get_user_model().objects.get(pk=options['actor_id'])
            resultado = cancelar_solicitud_sandbox(
                application_id=options['application_id'],
                actor=actor,
                motivo=options['reason'],
            )
        except CommandError:
            raise
        except (PermissionDenied, ValidationError, ObjectDoesNotExist) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            'SANDBOX_CANCELLED '
            f'application_id={resultado.solicitud_id} '
            f'reference={resultado.referencia_externa} '
            f'automation={resultado.procesos_automatizacion_cerrados} '
            f'signatures={resultado.procesos_firma_cancelados} '
            f'artifacts={resultado.artefactos_cancelados}'
        )
