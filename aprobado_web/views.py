from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def health_check_view(request):
    response = JsonResponse({'status': 'ok'})
    response['Cache-Control'] = 'no-store'
    return response


def portal_entrypoint_view(request):
    return render(
        request,
        'financiacion_educativa/institucional.html',
    )
