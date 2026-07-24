from decimal import Decimal

from financiacion_educativa.services.solicitudes import (
    DatosSolicitudFinanciacion,
    crear_solicitud_financiacion,
)
from instituciones.models import Institucion


def crear_institucion(sufijo='1'):
    return Institucion.objects.create(
        nombre_comercial=f'Institucion {sufijo}',
        razon_social=f'Institucion {sufijo} SAS',
        numero_identificacion_tributaria=f'90000000{sufijo}',
    )


def crear_solicitud(institucion=None, referencia='REF-001', usuario=None):
    institucion = institucion or crear_institucion()
    return crear_solicitud_financiacion(
        institucion=institucion,
        usuario=usuario,
        datos=DatosSolicitudFinanciacion(
            referencia_externa=referencia,
            nombres='ANA MARIA',
            apellidos='PEREZ LOPEZ',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 10 # 20-30',
            valor_plan=Decimal('1000000.00'),
            plazo_meses=12,
            nombre_curso='Tecnico en Sistemas',
            tipo_curso='TECNICO',
            correlation_id=f'corr-{referencia}',
        ),
    )
