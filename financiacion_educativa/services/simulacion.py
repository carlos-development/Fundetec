from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from financiacion_educativa.models import ConfiguracionFinancieraEducativa
from financiacion_educativa.services.configuracion_financiera import (
    seleccionar_configuracion_vigente,
)
from financiacion_educativa.services.reglas_financieras import (
    ResultadoCondicionesFinancieras,
    calcular_condiciones_financieras,
)


@dataclass(frozen=True)
class SimulacionFinanciacionEducativa:
    configuracion: ConfiguracionFinancieraEducativa
    resultado: ResultadoCondicionesFinancieras


def simular_financiacion_educativa(
    *,
    monto_solicitado,
    plazo_meses,
    fecha_aplicacion=None,
    fecha_inicio_plan=None,
):
    """Calcula un escenario educativo sin crear fotografias ni cuotas."""
    fecha_aplicacion = fecha_aplicacion or timezone.localdate()
    fecha_inicio_plan = fecha_inicio_plan or fecha_aplicacion
    if not isinstance(fecha_aplicacion, date):
        raise TypeError('fecha_aplicacion debe ser una fecha.')
    if not isinstance(fecha_inicio_plan, date):
        raise TypeError('fecha_inicio_plan debe ser una fecha.')

    configuracion = seleccionar_configuracion_vigente(
        fecha_aplicacion=fecha_aplicacion,
    )
    resultado = calcular_condiciones_financieras(
        monto_solicitado=monto_solicitado,
        plazo_meses=plazo_meses,
        configuracion=configuracion,
        fecha_inicio_plan=fecha_inicio_plan,
    )
    return SimulacionFinanciacionEducativa(
        configuracion=configuracion,
        resultado=resultado,
    )
