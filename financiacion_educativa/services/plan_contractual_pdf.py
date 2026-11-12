"""Read-only rendering of the saved contract; never a payoff calculation."""

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from financiacion_educativa.services.artefactos_contractuales import _renderizar_pdf


def datos_plan_contractual(fotografia):
    cuotas = list(fotografia.cuotas.order_by('numero').values(
        'numero', 'fecha_vencimiento', 'capital', 'interes', 'valor_cuota',
    ))
    totales = {campo: sum((c[campo] for c in cuotas), Decimal('0.00'))
        for campo in ('capital', 'interes', 'valor_cuota')}
    if (
        not fotografia.bloqueada or fotografia.es_legado
        or not fotografia.fecha_inicio_plan
        or not cuotas or len(cuotas) != fotografia.plazo_meses
        or [c['numero'] for c in cuotas] != list(range(1, fotografia.plazo_meses + 1))
        or cuotas[0]['fecha_vencimiento'] != fotografia.fecha_primer_vencimiento
        or cuotas[-1]['fecha_vencimiento'] != fotografia.fecha_ultimo_vencimiento
        or any(a['fecha_vencimiento'] >= b['fecha_vencimiento'] for a, b in zip(cuotas, cuotas[1:]))
        or any(c['capital'] + c['interes'] != c['valor_cuota'] for c in cuotas)
        or totales['capital'] != fotografia.capital_financiado
        or totales['interes'] != fotografia.interes_total_estimado
        or totales['valor_cuota'] != fotografia.total_estimado
    ):
        raise ValidationError('El calendario contractual guardado no esta completo o no es coherente.')
    return {'fotografia': fotografia, 'cuotas': cuotas, 'totales': totales,
        'logo_url': (Path(settings.BASE_DIR) / 'static/images/logo-dark.png').resolve().as_uri()}


def generar_plan_contractual_pdf(fotografia):
    return _renderizar_pdf('financiacion_educativa/documentos/plan_contractual_pdf.html',
        datos_plan_contractual(fotografia))
