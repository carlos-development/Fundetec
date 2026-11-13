from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.services.plan_contractual_pdf import generar_plan_contractual_pdf
from .views import _solicitud_del_usuario


@transaction.non_atomic_requests
@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def descargar_plan_view(request, solicitud_id, fotografia_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    fotografia = get_object_or_404(obligaciones_autorizadas(), solicitud_id=solicitud.pk, pk=fotografia_id)
    try:
        pdf = generar_plan_contractual_pdf(fotografia)
    except ValidationError:
        return render(request, 'financiacion_educativa/portal_estudiante/plan_no_disponible.html',
            {'solicitud': solicitud}, status=409)
    respuesta = HttpResponse(pdf, content_type='application/pdf')
    respuesta['Content-Disposition'] = f'attachment; filename="plan-contractual-v{fotografia.numero_version}.pdf"'
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['Referrer-Policy'] = 'no-referrer'
    respuesta['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return respuesta
