import uuid

from django.core.management.base import BaseCommand, CommandError

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoValidacionDocumento,
    OrigenValidacionIADocumento,
)
from financiacion_educativa.models import DocumentoFinanciacion
from financiacion_educativa.services.validacion_documental_ia import (
    procesar_validacion_documental_ia,
)


class Command(BaseCommand):
    help = 'Procesa validaciones IA pendientes de imagenes documentales seguras.'

    def add_arguments(self, parser):
        parser.add_argument('--documento-id', type=uuid.UUID)
        parser.add_argument('--solicitud-id', type=uuid.UUID)
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        if options['limit'] <= 0:
            raise CommandError('--limit debe ser positivo.')
        queryset = DocumentoFinanciacion.objects.filter(
            activo=True,
            estado_escaneo=EstadoEscaneoDocumento.SAFE,
            estado_validacion=EstadoValidacionDocumento.PENDING,
            content_type__in=('image/jpeg', 'image/png'),
        ).order_by('cargado_en')
        if options['documento_id']:
            queryset = queryset.filter(pk=options['documento_id'])
        if options['solicitud_id']:
            queryset = queryset.filter(solicitud_id=options['solicitud_id'])
        documentos = list(queryset[:options['limit']])
        if options['documento_id'] and not documentos:
            raise CommandError('No existe una imagen segura pendiente con ese identificador.')

        procesados = 0
        manuales = 0
        errores = 0
        deshabilitado = False
        for documento in documentos:
            resultado = procesar_validacion_documental_ia(
                documento=documento,
                origen=OrigenValidacionIADocumento.COMMAND,
            )
            procesados += int(resultado.procesado)
            manuales += int(resultado.estado == 'MANUAL_REVIEW')
            errores += int(bool(resultado.codigo_error))
            deshabilitado = deshabilitado or resultado.estado == 'DISABLED'
            if deshabilitado:
                break
        if deshabilitado:
            raise CommandError(
                'El backend de IA documental esta deshabilitado; no se crearon intentos.'
            )
        self.stdout.write(
            self.style.SUCCESS(
                f'Procesados: {procesados}; revision manual: {manuales}; errores: {errores}.'
            )
        )
