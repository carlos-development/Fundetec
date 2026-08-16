from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.services.calibracion_documental import (
    ErrorCalibracionDocumental,
    ejecutar_calibracion,
)


class Command(BaseCommand):
    help = (
        'Valida un dataset documental privado y genera un reporte sanitizado. '
        'No usa modelos del flujo educativo.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dataset', required=True)
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--private-context')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Valida rutas, manifest y archivos sin llamar proveedores.',
        )
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Ejecuta los backends de calibracion configurados.',
        )
        parser.add_argument(
            '--allow-real-openai',
            action='store_true',
            help=(
                'Segunda confirmacion requerida para backends OpenAI reales. '
                'Tambien exige habilitacion por entorno.'
            ),
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Reemplaza de forma atomica un reporte existente.',
        )

    def handle(self, *args, **options):
        if options['dry_run'] and options['execute']:
            raise CommandError('DRY_RUN_AND_EXECUTE_ARE_EXCLUSIVE')
        dry_run = not options['execute']
        try:
            report = ejecutar_calibracion(
                dataset=options['dataset'],
                manifest=options['manifest'],
                output=options['output'],
                private_context=options.get('private_context'),
                dry_run=dry_run,
                allow_real_openai=options['allow_real_openai'],
                overwrite=options['overwrite'],
            )
        except ErrorCalibracionDocumental as error:
            raise CommandError(error.codigo) from error
        metrics = report['metrics']
        self.stdout.write(
            self.style.SUCCESS(
                'Calibracion completada: '
                f"dry_run={str(report['dry_run']).lower()} "
                f"casos={metrics['total_cases']} "
                f"evaluados={metrics['evaluated_cases']} "
                f"falsos_aceptados={metrics['false_accepts']} "
                f"errores_tecnicos={metrics['technical_errors']}."
            )
        )
