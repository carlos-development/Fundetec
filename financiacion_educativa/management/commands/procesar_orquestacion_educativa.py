import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.orquestacion_automatica import (
    ejecutar_orquestacion_automatica,
)


class Command(BaseCommand):
    help = 'Retoma recorridos educativos automaticos pendientes o fallidos.'

    def add_arguments(self, parser):
        parser.add_argument('--solicitud-id', type=uuid.UUID)
        parser.add_argument('--limit', type=int, default=25)

    def handle(self, *args, **options):
        if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
            raise CommandError('La automatizacion educativa esta deshabilitada.')
        if options['limit'] <= 0:
            raise CommandError('--limit debe ser positivo.')
        queryset = SolicitudFinanciacionEducativa.objects.filter(
            estado__in=(
                EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
                EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
            )
        ).order_by('actualizada_en')
        if options['solicitud_id']:
            queryset = queryset.filter(pk=options['solicitud_id'])
        solicitudes = list(queryset[:options['limit']])
        if options['solicitud_id'] and not solicitudes:
            raise CommandError('La solicitud no admite reanudacion automatica.')
        resultados = {}
        for solicitud in solicitudes:
            resultado = ejecutar_orquestacion_automatica(
                solicitud_id=solicitud.pk
            )
            resultados[resultado.codigo] = resultados.get(resultado.codigo, 0) + 1
        resumen = '; '.join(
            f'{codigo}: {cantidad}'
            for codigo, cantidad in sorted(resultados.items())
        ) or 'sin solicitudes'
        self.stdout.write(self.style.SUCCESS(f'Procesadas: {len(solicitudes)}; {resumen}.'))
