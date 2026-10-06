import signal
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import OuterRef, Subquery, Q
from django.utils import timezone
from django.conf import settings

from financiacion_educativa.models_recaudos import ArchivoRecaudoEducativo, IntentoEscaneoRecaudo
from financiacion_educativa.services.recaudos import escanear_archivo_recaudo


class Command(BaseCommand):
    help = 'Escanea evidencias de recaudo (QR, comprobantes y evidencia bancaria), sin IA.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--archivo-id', type=str)
        parser.add_argument('--actor-id', type=str)
        parser.add_argument('--motivo', default='')
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--interval', type=int, default=10)

    def handle(self, *args, **options):
        if options['limit'] < 1 or options['interval'] < 1:
            raise CommandError('Limit e intervalo deben ser positivos.')
        actor = None
        if options['actor_id']:
            if not options['archivo_id'] or not options['once'] or not options['motivo'].strip():
                raise CommandError('El reintento manual requiere --once, --archivo-id y --motivo.')
            actor = get_user_model().objects.get(pk=options['actor_id'])
        if not settings.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED and not actor:
            raise CommandError('Recaudos deshabilitados; el escaneo previo del QR requiere actor y motivo explicitos.')
        detenido = False

        def detener(_signal, _frame):
            nonlocal detenido
            detenido = True

        previos = {sig: signal.signal(sig, detener) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            while not detenido:
                close_old_connections()
                archivos = ArchivoRecaudoEducativo.objects.order_by('creado_en', 'pk')
                if options['archivo_id']:
                    archivos = archivos.filter(pk=options['archivo_id'])
                else:
                    ultimo = IntentoEscaneoRecaudo.objects.filter(archivo_id=OuterRef('pk')).order_by('-numero')
                    archivos = archivos.annotate(
                        scan_estado=Subquery(ultimo.values('estado')[:1]),
                        scan_numero=Subquery(ultimo.values('numero')[:1]),
                        scan_inicio=Subquery(ultimo.values('iniciado_en')[:1]),
                    ).filter(
                        Q(scan_estado__isnull=True)
                        | Q(scan_estado='ERROR', scan_numero__lt=settings.FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS)
                        | Q(scan_estado='STARTED', scan_inicio__lte=timezone.now() - timedelta(seconds=settings.FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS)),
                    )
                procesados = 0
                for archivo_id in list(archivos.values_list('pk', flat=True)[:options['limit']]):
                    if detenido:
                        break
                    intento = escanear_archivo_recaudo(archivo_id=archivo_id, actor=actor, motivo=options['motivo'])
                    if intento:
                        procesados += 1
                        self.stdout.write(f'Archivo={archivo_id}; estado={intento.estado}; codigo={intento.codigo_error or "NONE"}')
                self.stdout.write(f'Procesados: {procesados}')
                if options['once']:
                    break
                for _ in range(options['interval']):
                    if detenido:
                        break
                    time.sleep(1)
        finally:
            close_old_connections()
            for sig, handler in previos.items():
                signal.signal(sig, handler)
