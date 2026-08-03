import uuid

from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    OrigenIntentoEscaneoDocumento,
)
from financiacion_educativa.models import DocumentoFinanciacion
from financiacion_educativa.services.escaneo_documentos import (
    procesar_escaneo_documento,
)


class Command(BaseCommand):
    help = 'Procesa o reintenta escaneos antivirus documentales pendientes.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--documento-id', type=uuid.UUID)
        parser.add_argument('--solicitud-id', type=uuid.UUID)

    def handle(self, *args, **options):
        limite = options['limit']
        if limite <= 0:
            raise CommandError('--limit debe ser positivo.')
        queryset = DocumentoFinanciacion.objects.filter(
            activo=True,
            estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        ).order_by('cargado_en')
        if options['documento_id']:
            queryset = queryset.filter(pk=options['documento_id'])
            if not queryset.exists():
                raise CommandError(
                    'No existe un documento pendiente con ese identificador.'
                )
        if options['solicitud_id']:
            queryset = queryset.filter(solicitud_id=options['solicitud_id'])

        conteos = {
            'procesados': 0,
            'seguros': 0,
            'bloqueados': 0,
            'errores': 0,
            'omitidos': 0,
        }
        for documento in queryset[:limite]:
            resultado = procesar_escaneo_documento(
                documento=documento,
                origen=OrigenIntentoEscaneoDocumento.COMMAND,
            )
            conteos['procesados'] += int(resultado.procesado)
            if resultado.estado == EstadoEscaneoDocumento.SAFE:
                conteos['seguros'] += 1
            elif resultado.estado == EstadoEscaneoDocumento.BLOCKED:
                conteos['bloqueados'] += 1
            elif resultado.codigo_error:
                conteos['errores'] += 1
            else:
                conteos['omitidos'] += 1
        self.stdout.write(
            self.style.SUCCESS(
                'Procesados: {procesados}; seguros: {seguros}; '
                'bloqueados: {bloqueados}; errores: {errores}; '
                'omitidos: {omitidos}.'.format(**conteos)
            )
        )
