import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.checks import ZAPSIGN_EDUCATIONAL_BACKEND
from financiacion_educativa.services.firma_zapsign import (
    ZapSignEducationalSignatureBackend,
)


BASES_ZAPSIGN = {
    'staging': {'https://sandbox.api.zapsign.com.br/api/v1'},
    'production': {
        'https://api.zapsign.com.br/api/v1',
        'https://br.api.zapsign.com.br/api/v1',
    },
}


class Command(BaseCommand):
    help = 'Comprueba la credencial educativa en ZapSign mediante un GET de solo lectura.'

    def add_arguments(self, parser):
        parser.add_argument('--entorno', choices=BASES_ZAPSIGN, required=True)

    def handle(self, *args, **options):
        entorno = options['entorno']
        if settings.DEPLOYMENT_ENVIRONMENT != entorno:
            raise CommandError('El entorno efectivo de Django no coincide con --entorno.')
        if settings.FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND != ZAPSIGN_EDUCATIONAL_BACKEND:
            raise CommandError('El backend educativo configurado no es el oficial de ZapSign.')
        try:
            backend = ZapSignEducationalSignatureBackend()
        except ImproperlyConfigured:
            raise CommandError('Falta la credencial educativa de ZapSign.') from None
        if backend.base_url not in BASES_ZAPSIGN[entorno]:
            raise CommandError('La URL de ZapSign no corresponde al entorno indicado.')

        try:
            with requests.get(
                f'{backend.base_url}/docs/',
                params={'page': 1},
                headers={'Authorization': f'Bearer {backend.api_token}'},
                timeout=backend.timeout,
                allow_redirects=False,
                stream=True,
            ) as respuesta:
                estado = respuesta.status_code
        except requests.RequestException:
            raise CommandError(f'ZapSign {entorno}: error de conexión.') from None

        if estado != 200:
            raise CommandError(f'ZapSign {entorno}: HTTP {estado}.')
        self.stdout.write(f'ZapSign {entorno}: HTTP 200.')
