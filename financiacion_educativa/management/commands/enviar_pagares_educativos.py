from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.choices import EstadoProcesoFirmaEducativa
from financiacion_educativa.models import ProcesoFirmaEducativa
from financiacion_educativa.services.firma_zapsign import (
    FirmaEducativaError,
    enviar_pagare_educativo,
)


class Command(BaseCommand):
    help = 'Envia pagares educativos pendientes mediante el backend configurado.'

    def add_arguments(self, parser):
        parser.add_argument('--solicitud-id')
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument(
            '--confirmar-reintento-permanente',
            action='store_true',
            help=(
                'Permite reintentar un rechazo HTTP 4xx despues de corregir '
                'la configuracion o el payload. No habilita envios ambiguos.'
            ),
        )

    def handle(self, *args, **options):
        if str(settings.FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND).endswith(
            'DisabledEducationalSignatureBackend'
        ):
            raise CommandError(
                'La integracion educativa de firma esta deshabilitada.'
            )
        limite = options['limit']
        if limite <= 0 or limite > 200:
            raise CommandError('--limit debe estar entre 1 y 200.')
        procesos = ProcesoFirmaEducativa.objects.filter(
            estado__in={
                EstadoProcesoFirmaEducativa.PENDING,
                EstadoProcesoFirmaEducativa.FAILED,
            },
        ).order_by('creado_en')
        solicitud_id = options.get('solicitud_id')
        if solicitud_id:
            procesos = procesos.filter(solicitud_id=solicitud_id)
        procesados = 0
        fallidos = 0
        for proceso in procesos[:limite]:
            try:
                enviar_pagare_educativo(
                    proceso=proceso,
                    permitir_reintento_permanente=options[
                        'confirmar_reintento_permanente'
                    ],
                )
            except (FirmaEducativaError, ValidationError):
                fallidos += 1
            else:
                procesados += 1
        if fallidos:
            raise CommandError(
                f'Enviados: {procesados}. Fallidos: {fallidos}.'
            )
        self.stdout.write(self.style.SUCCESS(f'Enviados: {procesados}.'))
