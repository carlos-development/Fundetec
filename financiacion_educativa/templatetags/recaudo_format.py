from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def cop_recaudo(valor):
    """Preserva los centavos declarados/recibidos; no redondea evidencia bancaria."""
    try:
        numero = Decimal(valor)
        if not numero.is_finite():
            return ''
        return '$' + format(numero, ',.2f').replace(',', '_').replace('.', ',').replace('_', '.')
    except (InvalidOperation, TypeError, ValueError):
        return ''
