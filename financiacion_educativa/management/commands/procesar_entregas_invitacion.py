from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEntregaInvitacion,
    EstadoSolicitudFinanciacion,
    OrigenEntregaInvitacion,
)
from financiacion_educativa.models import EntregaInvitacionContinuacion
from financiacion_educativa.services.orquestacion import (
    reemitir_invitacion_orquestada,
)


class Command(BaseCommand):
    help = (
        'Recupera entregas de invitacion fallidas o estancadas mediante '
        'reemision atomica de un enlace nuevo.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        limite = options['limit']
        if limite <= 0:
            raise CommandError('--limit debe ser positivo.')
        segundos = int(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_INVITATION_RECOVERY_STALE_SECONDS',
                300,
            )
        )
        if segundos < 0:
            raise CommandError(
                'FINANCIACION_EDUCATIVA_INVITATION_RECOVERY_STALE_SECONDS '
                'no puede ser negativo.'
            )
        corte = timezone.now() - timedelta(seconds=segundos)
        candidatas = (
            EntregaInvitacionContinuacion.objects.select_related('solicitud')
            .filter(
                solicitud__estado=(
                    EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION
                ),
                solicitud__usuario__isnull=True,
            )
            .filter(
                Q(estado=EstadoEntregaInvitacion.FAILED)
                | Q(
                    estado__in=[
                        EstadoEntregaInvitacion.PENDING,
                        EstadoEntregaInvitacion.SENDING,
                    ],
                    actualizada_en__lte=corte,
                )
            )
            .order_by('programada_en')[:limite]
        )
        procesadas = 0
        omitidas = 0
        solicitudes_vistas = set()
        for entrega in candidatas:
            if entrega.solicitud_id in solicitudes_vistas:
                continue
            solicitudes_vistas.add(entrega.solicitud_id)
            try:
                reemitir_invitacion_orquestada(
                    solicitud=entrega.solicitud,
                    origen=OrigenEntregaInvitacion.AUTOMATIC_RETRY,
                )
            except ValidationError:
                omitidas += 1
            else:
                procesadas += 1
        self.stdout.write(
            self.style.SUCCESS(
                f'Entregas recuperadas: {procesadas}; omitidas: {omitidas}.'
            )
        )
