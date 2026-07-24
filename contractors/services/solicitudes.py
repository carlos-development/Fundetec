from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from contractors.models import ContractorApplication


class ErrorSolicitudContratista(ValueError):
    pass


@dataclass(frozen=True)
class DatosSolicitudContratista:
    monto_solicitado: Decimal
    plazo_meses: int
    tipo_documento: str
    numero_documento: str
    nombres: str
    apellidos: str
    celular: str
    correo: str
    escenario_credito: str = ContractorApplication.EscenarioCredito.NUEVO_CREDITO
    direccion: str = ''
    terminos_aceptados: bool = False
    cuota_mensual_estimada: Decimal = Decimal('0.00')
    payload_simulacion: dict = field(default_factory=dict)
    subdominio_origen: str = ''
    ip_address: str | None = None
    user_agent: str = ''


@dataclass(frozen=True)
class ResultadoSolicitudContratista:
    solicitud: ContractorApplication

    @property
    def solicitud_id(self):
        return self.solicitud.id

    @property
    def estado(self):
        return self.solicitud.status


def crear_solicitud_contratista(
    *,
    organizacion=None,
    configuracion_producto=None,
    configuracion_portal=None,
    datos=None,
    usuario=None,
):
    if organizacion is None and configuracion_portal is None:
        raise ErrorSolicitudContratista('organizacion_o_configuracion_portal_requerida')
    if configuracion_producto is None and configuracion_portal is None:
        raise ErrorSolicitudContratista('configuracion_producto_requerida')
    if not isinstance(datos, DatosSolicitudContratista):
        raise ErrorSolicitudContratista('datos_solicitud_invalidos')

    solicitud = ContractorApplication(
        organization=organizacion,
        configuracion_portal=configuracion_portal,
        product_config=configuracion_producto,
        usuario=usuario,
        status=ContractorApplication.Estado.RECIBIDA,
        escenario_credito=datos.escenario_credito,
        requested_amount=datos.monto_solicitado,
        term_months=datos.plazo_meses,
        estimated_monthly_payment=datos.cuota_mensual_estimada,
        simulation_payload=datos.payload_simulacion or {},
        document_type=datos.tipo_documento,
        document_number=datos.numero_documento,
        first_name=datos.nombres,
        last_name=datos.apellidos,
        phone=datos.celular,
        email=datos.correo,
        address=datos.direccion,
        accepted_terms=datos.terminos_aceptados,
        source_subdomain=datos.subdominio_origen or getattr(organizacion, 'subdomain', '') or getattr(configuracion_portal, 'slug', ''),
        ip_address=datos.ip_address,
        user_agent=datos.user_agent,
    )

    try:
        solicitud.full_clean()
    except ValidationError:
        raise

    with transaction.atomic():
        solicitud.save()

    return ResultadoSolicitudContratista(solicitud=solicitud)
