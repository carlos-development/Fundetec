import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.models import DocumentoFinanciacion
from financiacion_educativa.services.escaneo_documentos import (
    reabrir_escaneo_documento,
)


class Command(BaseCommand):
    help = 'Reabre de forma auditada el presupuesto de escaneo de un documento.'

    def add_arguments(self, parser):
        parser.add_argument('--documento-id', type=uuid.UUID, required=True)
        parser.add_argument('--actor-id', required=True)
        parser.add_argument('--motivo', required=True)

    def handle(self, *args, **options):
        try:
            documento = DocumentoFinanciacion.objects.get(
                pk=options['documento_id']
            )
        except DocumentoFinanciacion.DoesNotExist as error:
            raise CommandError('No existe el documento indicado.') from error
        try:
            actor = get_user_model().objects.get(pk=options['actor_id'])
        except (get_user_model().DoesNotExist, ValueError) as error:
            raise CommandError('No existe el actor indicado.') from error

        try:
            reapertura = reabrir_escaneo_documento(
                documento=documento,
                actor=actor,
                motivo=options['motivo'],
            )
        except (PermissionDenied, ValidationError) as error:
            mensajes = getattr(error, 'messages', [str(error)])
            raise CommandError('; '.join(mensajes)) from error

        self.stdout.write(
            self.style.SUCCESS(
                'Reapertura registrada para el documento '
                f'{reapertura.documento_id}; intentos adicionales: '
                f'{reapertura.intentos_adicionales}.'
            )
        )
