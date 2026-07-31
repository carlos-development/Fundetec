from decimal import Decimal
from datetime import date

from financiacion_educativa.choices import (
    EstadoConfiguracionFinanciera,
    MetodoCalculoFinanciero,
    PoliticaCausacionInteres,
    PoliticaRedondeoFinanciero,
)
from financiacion_educativa.models import ConfiguracionFinancieraEducativa
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


def crear_solicitud(
    institucion=None,
    referencia='REF-001',
    usuario=None,
    correo=None,
):
    institucion = institucion or crear_institucion()
    return crear_solicitud_financiacion(
        institucion=institucion,
        usuario=usuario,
        datos=DatosSolicitudFinanciacion(
            referencia_externa=referencia,
            nombres='ANA MARIA',
            apellidos='PEREZ LOPEZ',
            celular='3001234567',
            correo=correo or (
                usuario.email if usuario is not None else 'ana@example.com'
            ),
            direccion='Calle 10 # 20-30',
            valor_plan=Decimal('1000000.00'),
            plazo_meses=12,
            nombre_curso='Tecnico en Sistemas',
            tipo_curso='TECNICO',
            correlation_id=f'corr-{referencia}',
        ),
    )


def crear_configuracion_financiera(
    *,
    version=1,
    vigente_desde=date(2026, 1, 1),
    vigente_hasta=None,
    estado=EstadoConfiguracionFinanciera.ACTIVE,
    tasa_interes=Decimal('1'),
):
    return ConfiguracionFinancieraEducativa.objects.create(
        codigo='EDU_STANDARD',
        version=version,
        vigente_desde=vigente_desde,
        vigente_hasta=vigente_hasta,
        estado=estado,
        porcentaje_originacion=Decimal('10'),
        porcentaje_iva_originacion=Decimal('19'),
        porcentaje_fondo_garantias=Decimal('2'),
        proveedor_fondo_garantias='Figarantias',
        porcentaje_seguro_vida=Decimal('0.3711'),
        proveedor_seguro_vida='SURA',
        tasa_interes_mensual=tasa_interes,
        moneda='COP',
        metodo_calculo=MetodoCalculoFinanciero.FRENCH_AMORTIZATION,
        politica_redondeo=PoliticaRedondeoFinanciero.COP_PESO_HALF_UP,
        politica_causacion=PoliticaCausacionInteres.DAILY_30,
    )
