import os
import stat
from pathlib import Path

from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def agregar_argumentos_entrega_token(parser):
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        '--mostrar-token',
        action='store_true',
        help='Muestra el token una sola vez en stdout.',
    )
    grupo.add_argument(
        '--archivo-token',
        help=(
            'Escribe el token en un archivo nuevo, absoluto y exclusivo 0600; '
            'no lo muestra en stdout.'
        ),
    )


def agregar_argumentos_expiracion(parser, *, permitir_sin_expiracion=False):
    parser.add_argument(
        '--expira-en',
        help='Fecha ISO-8601 futura con zona horaria, por ejemplo 2026-12-31T23:59:59-05:00.',
    )
    if permitir_sin_expiracion:
        parser.add_argument(
            '--sin-expiracion',
            action='store_true',
            help='Elimina expresamente la fecha de expiracion al rotar.',
        )


def resolver_expiracion(valor):
    if not valor:
        return None
    expira_en = parse_datetime(valor)
    if expira_en is None or timezone.is_naive(expira_en):
        raise CommandError(
            '--expira-en debe ser una fecha ISO-8601 valida con zona horaria.'
        )
    if expira_en <= timezone.now():
        raise CommandError('--expira-en debe ser una fecha futura.')
    return expira_en


def _reservar_archivo_token(valor):
    ruta = Path(valor).expanduser()
    if not ruta.is_absolute():
        raise CommandError('--archivo-token debe ser una ruta absoluta.')
    if ruta.exists() or ruta.is_symlink():
        raise CommandError('El archivo de token ya existe; no sera sobrescrito.')

    padre = ruta.parent
    try:
        padre_resuelto = padre.resolve(strict=True)
    except OSError as exc:
        raise CommandError('El directorio del archivo de token no existe.') from exc
    if padre_resuelto != padre or padre.is_symlink() or not padre.is_dir():
        raise CommandError('El directorio del archivo de token no es seguro.')

    if os.name == 'posix':
        estado_padre = padre.stat()
        if estado_padre.st_uid != os.geteuid():
            raise CommandError(
                'El directorio del archivo de token debe pertenecer al usuario actual.'
            )
        if stat.S_IMODE(estado_padre.st_mode) & 0o077:
            raise CommandError(
                'El directorio del archivo de token debe tener permiso 0700 o mas restrictivo.'
            )

    banderas = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    banderas |= getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(str(ruta), banderas, 0o600)
    except OSError as exc:
        raise CommandError('No fue posible reservar el archivo de token.') from exc
    return ruta, descriptor


def _escribir_token(descriptor, token):
    contenido = f'{token}\n'.encode('utf-8')
    desplazamiento = 0
    while desplazamiento < len(contenido):
        escritos = os.write(descriptor, contenido[desplazamiento:])
        if escritos <= 0:
            raise OSError('No fue posible completar la escritura del token.')
        desplazamiento += escritos
    os.fsync(descriptor)


def _validar_modo_descriptor(descriptor):
    if os.name != 'posix':
        return
    if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
        raise CommandError('El archivo de token no quedo con permiso 0600.')


def ejecutar_emision_con_entrega(
    *,
    command,
    operacion,
    mostrar_token,
    archivo_token,
):
    if mostrar_token:
        emitida = operacion()
        _informar_credencial(command, emitida.credencial)
        command.stdout.write(f'TOKEN_UNICA_VEZ={emitida.token}')
        command.stdout.write(
            command.style.WARNING(
                'Guarde el token ahora en un gestor de secretos; no puede recuperarse.'
            )
        )
        return emitida.credencial

    ruta, descriptor = _reservar_archivo_token(archivo_token)
    descriptor_abierto = True
    try:
        with transaction.atomic():
            emitida = operacion()
            _escribir_token(descriptor, emitida.token)
            _validar_modo_descriptor(descriptor)
            os.close(descriptor)
            descriptor_abierto = False
    except Exception:
        if descriptor_abierto:
            os.close(descriptor)
        ruta.unlink(missing_ok=True)
        raise

    _informar_credencial(command, emitida.credencial)
    command.stdout.write(f'TOKEN_ARCHIVO={ruta}')
    command.stdout.write(
        command.style.WARNING(
            'Importe el archivo en un gestor de secretos y eliminelo de forma segura.'
        )
    )
    return emitida.credencial


def _informar_credencial(command, credencial):
    command.stdout.write(f'CREDENCIAL_ID={credencial.id}')
    command.stdout.write(f'INSTITUCION_ID={credencial.institucion_id}')
    command.stdout.write(f'PREFIJO={credencial.prefijo_clave}')
