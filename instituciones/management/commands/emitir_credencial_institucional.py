from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from instituciones.management.credential_io import (
    agregar_argumentos_entrega_token,
    agregar_argumentos_expiracion,
    ejecutar_emision_con_entrega,
    resolver_expiracion,
)
from instituciones.models import Institucion
from instituciones.services.credenciales import crear_credencial_api


class Command(BaseCommand):
    help = 'Emite una credencial institucional y entrega su token una sola vez.'

    def add_arguments(self, parser):
        parser.add_argument('--institucion-id', type=UUID, required=True)
        parser.add_argument('--nombre', required=True)
        parser.add_argument(
            '--prefijo',
            help=(
                'Prefijo opcional, unico y de hasta 16 caracteres. Se normaliza '
                'a minusculas y solo admite letras, numeros, guion y guion bajo.'
            ),
        )
        parser.add_argument(
            '--alcance',
            action='append',
            default=[],
            help='Metadato repetible; los alcances aun no son aplicados por permisos.',
        )
        agregar_argumentos_expiracion(parser)
        agregar_argumentos_entrega_token(parser)

    def handle(self, *args, **options):
        try:
            institucion = Institucion.objects.get(pk=options['institucion_id'])
        except Institucion.DoesNotExist as exc:
            raise CommandError('La institucion indicada no existe.') from exc

        nombre = options['nombre'].strip()
        if not nombre:
            raise CommandError('--nombre no puede estar vacio.')
        expira_en = resolver_expiracion(options['expira_en'])

        try:
            ejecutar_emision_con_entrega(
                command=self,
                mostrar_token=options['mostrar_token'],
                archivo_token=options['archivo_token'],
                operacion=lambda: crear_credencial_api(
                    institucion=institucion,
                    nombre=nombre,
                    alcances=options['alcance'],
                    expira_en=expira_en,
                    prefijo=options['prefijo'],
                ),
            )
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        except IntegrityError as exc:
            raise CommandError(
                'No fue posible emitir la credencial por un conflicto de unicidad.'
            ) from exc
