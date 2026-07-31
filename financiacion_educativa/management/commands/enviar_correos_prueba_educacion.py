from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from financiacion_educativa.services.correos import (
    construir_correos_prueba,
    validar_configuracion_smtp,
)


class Command(BaseCommand):
    help = (
        'Envia una muestra inerte de cada correo educativo a un destinatario '
        'explicito.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--destinatario', required=True)
        parser.add_argument(
            '--confirmar',
            action='store_true',
            help='Confirma que se autoriza el envio SMTP real de nueve muestras.',
        )

    def handle(self, *args, **options):
        destinatario = options['destinatario'].strip()
        if not options['confirmar']:
            raise CommandError(
                'El envio no fue ejecutado. Agrega --confirmar para autorizarlo.'
            )
        try:
            validate_email(destinatario)
        except ValidationError as exc:
            raise CommandError('El destinatario no es una direccion valida.') from exc

        try:
            validar_configuracion_smtp()
        except Exception as exc:
            raise CommandError(
                'El envio no fue ejecutado: configuracion SMTP invalida.'
            ) from exc

        connection = get_connection(timeout=settings.EMAIL_TIMEOUT)
        try:
            connection.open()
        except Exception as exc:
            raise CommandError(
                'No fue posible establecer una conexion SMTP segura.'
            ) from exc

        fallos = []
        try:
            for mensaje in construir_correos_prueba(
                destinatario=destinatario,
                connection=connection,
            ):
                codigo = mensaje.extra_headers.get(
                    'X-Aprobado-Sample',
                    'captura-movil',
                )
                try:
                    enviado = mensaje.send(fail_silently=False)
                except Exception:
                    enviado = 0
                if enviado == 1:
                    self.stdout.write(self.style.SUCCESS(f'ACEPTADO {codigo}'))
                else:
                    fallos.append(codigo)
                    self.stdout.write(self.style.ERROR(f'FALLO {codigo}'))
        finally:
            connection.close()

        if fallos:
            raise CommandError(
                'Uno o mas correos no fueron aceptados por el servidor SMTP.'
            )
        self.stdout.write(self.style.SUCCESS('ACEPTADOS 9/9'))
