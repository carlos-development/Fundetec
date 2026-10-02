import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from financiacion_educativa.choices import EstadoVersionTerminos
from financiacion_educativa.models import VersionTerminosFinanciacion


class Command(BaseCommand):
    help = 'Carga versiones juridicas como borradores, sin publicarlas ni sustituir historicos.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, type=Path)
        parser.add_argument('--confirm', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            registros = json.loads(options['file'].read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError) as error:
            raise CommandError('No fue posible leer el JSON de borradores.') from error
        campos = {'tipo', 'version', 'titulo', 'contenido', 'obligatorio'}
        if not isinstance(registros, list) or not registros:
            raise CommandError('El JSON debe contener una lista no vacia de versiones.')
        versiones = set()
        pendientes = []
        for registro in registros:
            if not isinstance(registro, dict) or set(registro) != campos:
                raise CommandError('Cada registro requiere tipo, version, titulo, contenido y obligatorio.')
            if (
                not all(isinstance(registro[campo], str) for campo in campos - {'obligatorio'})
                or type(registro['obligatorio']) is not bool
            ):
                raise CommandError('Los textos deben ser cadenas y obligatorio debe ser booleano.')
            if registro['version'] in versiones:
                raise CommandError('El JSON contiene versiones duplicadas.')
            versiones.add(registro['version'])
            existente = VersionTerminosFinanciacion.objects.select_for_update().filter(
                version=registro['version']
            ).first()
            if existente:
                if any(getattr(existente, campo) != registro[campo] for campo in campos):
                    raise CommandError(
                        f'La version {registro["version"]} ya existe con otro contenido. '
                        'Revise el borrador o use una version nueva.'
                    )
                self.stdout.write(f'EXISTENTE={existente.version} ESTADO={existente.estado}')
                continue
            version = VersionTerminosFinanciacion(
                **registro,
                estado=EstadoVersionTerminos.DRAFT,
                hash_integridad=VersionTerminosFinanciacion.calcular_hash(registro['contenido']),
            )
            try:
                version.full_clean()
            except ValidationError as error:
                raise CommandError(f'Borrador invalido: {registro["version"]}.') from error
            pendientes.append(version)
        for version in pendientes:
            if options['confirm']:
                version.save()
            self.stdout.write(
                f'{"BORRADOR_CREADO" if options["confirm"] else "PREVISTA"}={version.version}'
            )
        self.stdout.write('PUBLICADAS=0')
        if not options['confirm']:
            self.stdout.write('SIN_CAMBIOS: agregue --confirm para cargar borradores.')
