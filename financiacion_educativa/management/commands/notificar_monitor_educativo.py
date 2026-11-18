"""Deliver aggregate operational alerts from the staging monitor."""

from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from smtplib import (
    SMTPAuthenticationError, SMTPConnectError, SMTPDataError, SMTPHeloError,
    SMTPRecipientsRefused, SMTPSenderRefused,
)

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


_SMTP_RECHAZADO = (
    SMTPAuthenticationError, SMTPConnectError, SMTPDataError, SMTPHeloError,
    SMTPRecipientsRefused, SMTPSenderRefused,
)
_BACKEND = 'aprobado_web.email_backends.SafeRoutingEmailBackend'


def _guardar_estado(ruta, estado):
    temporal = ruta.with_suffix('.tmp')
    with temporal.open('w', encoding='utf-8') as archivo:
        json.dump(estado, archivo, sort_keys=True)
        archivo.flush()
        os.fsync(archivo.fileno())
    temporal.replace(ruta)
    descriptor = os.open(ruta.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _revisar_alertas(datos, destinatario, ruta):
    anterior = json.loads(ruta.read_text(encoding='utf-8')) if ruta.exists() else {}
    if not isinstance(anterior, dict) or any(valor not in ('enviado', 'incierto') for valor in anterior.values()):
        raise ValueError('Formato de estado inválido.')

    alertas = {}
    for alerta in datos['alertas']:
        codigo = alerta['codigo']
        # El paso del diagnóstico identifica la unidad en alertas de servicios.
        unidad = alerta['primer_paso'] if codigo in ('SERVICE_DOWN', 'SERVICE_CHECK_UNAVAILABLE') else ''
        clave = sha256(f'{destinatario}\0{codigo}\0{unidad}'.encode()).hexdigest()
        alertas[clave] = alerta

    # Una lectura incompleta de BD no demuestra que los avisos previos se resolvieron.
    resueltas = anterior.keys() - alertas.keys() if not datos.get('diagnostico_incompleto') else set()
    vigente = {clave: valor for clave, valor in anterior.items() if clave not in resueltas}
    nuevas = alertas.keys() - vigente.keys()
    if nuevas:
        reservado = {**vigente, **dict.fromkeys(nuevas, 'incierto')}
        lineas = ['El monitor operativo de FUNDETEC detectó estos hallazgos en staging.', '']
        for clave in sorted(nuevas, key=lambda k: (alertas[k]['codigo'], alertas[k]['primer_paso'])):
            alerta = alertas[clave]
            lineas.extend([
                f"{alerta['severidad']} · {alerta['codigo']} · Casos {alerta['cantidad']}",
                f"Responsable sugerido: {alerta['responsable']}", alerta['primer_paso'], '',
            ])
        # Reservar antes de SMTP evita un segundo envío si el proceso pierde la respuesta.
        _guardar_estado(ruta, reservado)
        try:
            cantidad = EmailMessage(
                '[FUNDETEC staging] Alertas operativas', '\n'.join(lineas), to=[destinatario],
            ).send(fail_silently=False)
        except _SMTP_RECHAZADO:
            _guardar_estado(ruta, vigente)
            raise CommandError('ALERTA_SMTP_RECHAZADA', returncode=4) from None
        except Exception:
            raise CommandError('ALERTA_SMTP_INCIERTA', returncode=4) from None
        if cantidad != 1:
            raise CommandError('ALERTA_SMTP_INCIERTA', returncode=4)
        vigente.update(dict.fromkeys(nuevas, 'enviado'))
        _guardar_estado(ruta, vigente)
    elif vigente != anterior:
        _guardar_estado(ruta, vigente)

    return bool(nuevas), 'incierto' in vigente.values()


class Command(BaseCommand):
    help = 'Notifica por SMTP los hallazgos nuevos del monitor educativo en staging.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--destinatario', required=True)
        parser.add_argument('--servicio', action='append', required=True)

    def handle(self, *, destinatario, servicio, **options):
        if settings.DEPLOYMENT_ENVIRONMENT != 'staging' or settings.EMAIL_BACKEND != _BACKEND:
            raise CommandError('El notificador requiere staging y el backend SMTP de enrutamiento seguro.', returncode=4)
        try:
            validate_email(destinatario)
        except ValidationError:
            raise CommandError('Destinatario de alertas inválido.', returncode=4) from None
        if settings.EMAIL_QA_MODE and settings.EMAIL_QA_REDIRECT_TO != destinatario:
            raise CommandError('El destino QA de este proceso debe coincidir con el destinatario.', returncode=4)

        from fcntl import LOCK_EX, LOCK_NB, flock

        directorio = os.environ.get('STATE_DIRECTORY')
        if not directorio or not Path(directorio).is_dir():
            raise CommandError('Ejecute desde la unidad systemd con StateDirectory.', returncode=4)
        ruta = Path(directorio) / 'alertas.json'
        try:
            with ruta.with_suffix('.lock').open('a', encoding='utf-8') as bloqueo:
                flock(bloqueo, LOCK_EX | LOCK_NB)
                self._ejecutar(servicio, destinatario, ruta)
        except (OSError, ValueError):
            raise CommandError('ALERTA_ESTADO_O_DIAGNOSTICO_NO_DISPONIBLE', returncode=4) from None

    def _ejecutar(self, servicio, destinatario, ruta):
        salida = StringIO()
        diagnostico = None
        try:
            call_command('diagnosticar_operacion_educativa', servicio=servicio, stdout=salida)
        except CommandError as error:
            if error.returncode not in (2, 3) or not salida.getvalue():
                raise
            diagnostico = error
        self.stdout.write(salida.getvalue().strip())
        envio, incierto = _revisar_alertas(json.loads(salida.getvalue()), destinatario, ruta)
        self.stdout.write('ALERTA_SMTP_ACEPTADA' if envio else 'SIN_ALERTAS_NUEVAS')
        if incierto:
            raise CommandError('ALERTA_SMTP_INCIERTA; revisar la entrega en el proveedor.', returncode=4)
        if diagnostico is not None:
            raise diagnostico
