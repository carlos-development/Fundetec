from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from instituciones.management.credential_io import (
    agregar_argumentos_entrega_token,
    agregar_argumentos_expiracion,
    ejecutar_emision_con_entrega,
    resolver_expiracion,
)
from instituciones.models import CredencialAPIInstitucion
from instituciones.services.credenciales import rotar_credencial_api


class Command(BaseCommand):
    help = 'Rota una credencial, invalida el secreto anterior y entrega el nuevo una vez.'

    def add_arguments(self, parser):
        parser.add_argument('--credencial-id', type=UUID, required=True)
        parser.add_argument('--confirmar', action='store_true')
        agregar_argumentos_expiracion(parser, permitir_sin_expiracion=True)
        agregar_argumentos_entrega_token(parser)

    def handle(self, *args, **options):
        if not options['confirmar']:
            raise CommandError('La rotacion requiere --confirmar.')
        if options['expira_en'] and options['sin_expiracion']:
            raise CommandError(
                '--expira-en y --sin-expiracion son mutuamente excluyentes.'
            )
        try:
            credencial = CredencialAPIInstitucion.objects.select_related(
                'institucion'
            ).get(pk=options['credencial_id'])
        except CredencialAPIInstitucion.DoesNotExist as exc:
            raise CommandError('La credencial indicada no existe.') from exc

        if options['expira_en']:
            expira_en = resolver_expiracion(options['expira_en'])
        elif options['sin_expiracion']:
            expira_en = None
        else:
            expira_en = credencial.expira_en

        ejecutar_emision_con_entrega(
            command=self,
            mostrar_token=options['mostrar_token'],
            archivo_token=options['archivo_token'],
            operacion=lambda: rotar_credencial_api(
                credencial=credencial,
                expira_en=expira_en,
            ),
        )
