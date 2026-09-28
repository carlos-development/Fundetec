from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox, validar_ambiente_sandbox


class Command(BaseCommand):
    help = 'Certificacion Sandbox exclusivamente. No aprueba solicitudes reales ni envia comunicaciones.'

    def add_arguments(self, parser):
        parser.add_argument('--application-id', required=True, type=UUID)
        parser.add_argument('--actor-id', required=True, type=int)
        parser.add_argument('--status', choices=['APPROVED', 'REJECTED'])
        parser.add_argument('--decision-reason', default='')
        parser.add_argument('--confirm', action='store_true')
        parser.add_argument('--clear', action='store_true')

    def handle(self, *args, **options):
        try:
            validar_ambiente_sandbox()
            if not options['confirm']:
                raise CommandError('Se requiere --confirm. Solo certificacion Sandbox.')
            actor = get_user_model().objects.get(pk=options['actor_id'])
            anterior, resultado = simular_resultado_sandbox(
                application_id=options['application_id'], actor=actor,
                status=options['status'], decision_reason=options['decision_reason'], clear=options['clear'],
            )
        except PermissionDenied as error:
            raise CommandError(str(error)) from error
        except (ValidationError, ObjectDoesNotExist) as error:
            raise CommandError('Operacion Sandbox rechazada: ambiente, permisos, parametros o registro invalido.') from error
        self.stdout.write(
            f'SANDBOX application_id={options["application_id"]} anterior={anterior} '
            f'final={resultado.estado} course_authorized={resultado.curso_autorizado} '
            f'decision_reason={resultado.motivo_decision}'
        )
