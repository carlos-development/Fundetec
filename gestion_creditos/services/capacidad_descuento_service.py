"""Legacy facade for payroll deduction capacity calculations.

The implementation lives in ``libranza.services.payment_capacity``. Keep this
module for backward-compatible imports from existing views/services.
"""

from libranza.services.payment_capacity import (
    TWOPLACES,
    _round_down_money,
    _round_money,
    _to_decimal,
    calcular_capacidad_descuento,
    obtener_porcentaje_capacidad_descuento,
    simular_adelanto_nomina,
)


__all__ = [
    'TWOPLACES',
    'calcular_capacidad_descuento',
    'obtener_porcentaje_capacidad_descuento',
    'simular_adelanto_nomina',
]
