from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from instituciones.models import Institucion

from financiacion_educativa.services.limpieza_solicitudes import (
    ErrorLimpiezaSolicitudes,
    construir_plan_limpieza,
    ejecutar_limpieza_solicitudes,
)


class Command(BaseCommand):
    help = (
        'Inspecciona o elimina solicitudes educativas de una sola institucion '
        'con confirmaciones reforzadas.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--institucion-id', type=UUID, required=True)
        modo = parser.add_mutually_exclusive_group()
        modo.add_argument('--dry-run', action='store_true')
        modo.add_argument('--execute', action='store_true')
        parser.add_argument('--expected-count', type=int)
        parser.add_argument('--confirm', type=UUID)
        parser.add_argument('--expected-database')

    def _validar_entorno(self, options):
        entorno = str(
            getattr(settings, 'DEPLOYMENT_ENVIRONMENT', '') or ''
        ).strip().lower()
        if entorno == 'production':
            raise CommandError(
                'Este comando esta prohibido en produccion.'
            )
        base_real = str(connection.settings_dict.get('NAME') or '')
        base_esperada = str(options.get('expected_database') or '').strip()
        if base_esperada and base_real != base_esperada:
            raise CommandError(
                'La base activa no coincide con --expected-database.'
            )
        if options['execute']:
            faltantes = [
                nombre
                for nombre, valor in (
                    ('--expected-count', options.get('expected_count')),
                    ('--confirm', options.get('confirm')),
                    ('--expected-database', base_esperada),
                )
                if valor is None or valor == ''
            ]
            if faltantes:
                raise CommandError(
                    'La ejecucion requiere ' + ', '.join(faltantes) + '.'
                )
            if options['expected_count'] <= 0:
                raise CommandError('--expected-count debe ser mayor que cero.')
        return entorno or 'desconocido', base_real

    def _mostrar_plan(self, plan, *, entorno, base, modo):
        self.stdout.write(f'MODO={modo}')
        self.stdout.write(f'ENTORNO={entorno}')
        self.stdout.write(f'DATABASE={base}')
        self.stdout.write(f'INSTITUCION_ID={plan.institucion_id}')
        self.stdout.write(f'INSTITUCION={plan.institucion_nombre}')
        self.stdout.write(f'SOLICITUDES={len(plan.solicitudes)}')
        for _, referencia, estado in plan.solicitudes:
            self.stdout.write(f'SOLICITUD={referencia}|ESTADO={estado}')
        for modelo, cantidad in plan.conteos:
            self.stdout.write(f'MODELO={modelo}|CANTIDAD={cantidad}')
        total_conocido = sum(
            archivo.tamano
            for archivo in plan.archivos
            if archivo.tamano is not None
        )
        tamanos_desconocidos = sum(
            archivo.tamano is None for archivo in plan.archivos
        )
        self.stdout.write(f'ARCHIVOS_PRIVADOS={len(plan.archivos)}')
        self.stdout.write(f'ARCHIVOS_BYTES_APROX={total_conocido}')
        self.stdout.write(
            f'ARCHIVOS_TAMANO_DESCONOCIDO={tamanos_desconocidos}'
        )
        self.stdout.write(
            f'ARCHIVOS_NOMBRE_INVALIDO={len(plan.archivos_invalidos)}'
        )
        self.stdout.write(
            f'RELACIONES_PROTECT_INTERNAS={len(plan.relaciones_protegidas)}'
        )
        for relacion in plan.relaciones_protegidas:
            self.stdout.write(f'PROTECT={relacion}')
        self.stdout.write('PRESERVAR_INSTITUCION=1')
        self.stdout.write(f'PRESERVAR_CREDENCIALES={plan.credenciales}')
        self.stdout.write(
            f'PRESERVAR_CREDENCIALES_ACTIVAS={plan.credenciales_activas}'
        )
        self.stdout.write(f'PRESERVAR_MEMBRESIAS={plan.membresias}')
        self.stdout.write(
            f'PRESERVAR_USUARIOS_MEMBRESIA={plan.usuarios_membresia}'
        )

    def handle(self, *args, **options):
        entorno, base_real = self._validar_entorno(options)
        try:
            institucion = Institucion.objects.get(
                pk=options['institucion_id']
            )
        except Institucion.DoesNotExist as error:
            raise CommandError(
                'La institucion indicada no existe.'
            ) from error
        try:
            plan = construir_plan_limpieza(institucion)
        except ErrorLimpiezaSolicitudes as error:
            raise CommandError(str(error)) from error
        self._mostrar_plan(
            plan,
            entorno=entorno,
            base=base_real,
            modo='EXECUTE' if options['execute'] else 'DRY_RUN',
        )
        if not options['execute']:
            self.stdout.write('RESULTADO=SIN_CAMBIOS')
            return
        if options['confirm'] != institucion.pk:
            raise CommandError(
                '--confirm debe coincidir exactamente con --institucion-id.'
            )
        if options['expected_count'] != len(plan.solicitudes):
            raise CommandError(
                'La cantidad actual no coincide con --expected-count.'
            )
        try:
            resultado = ejecutar_limpieza_solicitudes(
                institucion_id=institucion.pk,
                expected_count=options['expected_count'],
            )
        except ErrorLimpiezaSolicitudes as error:
            raise CommandError(str(error)) from error
        for modelo, cantidad in resultado.eliminados_por_modelo:
            self.stdout.write(f'ELIMINADO={modelo}|CANTIDAD={cantidad}')
        self.stdout.write(
            f'ARCHIVOS_ELIMINADOS={len(resultado.archivos.eliminados)}'
        )
        self.stdout.write(
            f'ARCHIVOS_PRESERVADOS={len(resultado.archivos.preservados)}'
        )
        self.stdout.write(
            f'ARCHIVOS_NO_ELIMINADOS={len(resultado.archivos.fallidos)}'
        )
        for nombre, codigo in resultado.archivos.fallidos:
            self.stderr.write(
                f'ARCHIVO_NO_ELIMINADO={nombre}|ERROR={codigo}'
            )
        self.stdout.write('SOLICITUDES_RESTANTES=0')
        self.stdout.write('INSTITUCION_PRESERVADA=1')
        self.stdout.write('RESULTADO=COMPLETADO')
