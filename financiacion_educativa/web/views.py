from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import (
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.asociacion import (
    asociar_usuario_mediante_invitacion,
)
from financiacion_educativa.services.invitaciones import (
    InvitacionNoValida,
    obtener_invitacion_vigente_por_id,
    obtener_invitacion_vigente_por_token,
)
from financiacion_educativa.services.terminos import (
    aceptar_terminos_solicitud,
    obtener_versiones_terminos_vigentes,
)

from .forms import AccesoFinanciacionForm, RegistroFinanciacionForm


SESSION_INVITACION_ID = 'financiacion_educativa_invitacion_id'


def _sin_referer(response):
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _render_invitacion_invalida(request):
    request.session.pop(SESSION_INVITACION_ID, None)
    return _sin_referer(
        render(
            request,
            'financiacion_educativa/invitacion_invalida.html',
            status=410,
        )
    )


def _invitacion_de_sesion(request):
    return obtener_invitacion_vigente_por_id(
        request.session.get(SESSION_INVITACION_ID)
    )


@never_cache
@require_GET
def continuar_invitacion_view(request, token):
    invitacion = obtener_invitacion_vigente_por_token(token)
    if not invitacion:
        return _render_invitacion_invalida(request)

    request.session[SESSION_INVITACION_ID] = str(invitacion.pk)
    return _sin_referer(
        redirect('financiacion_educativa_web:inicio')
    )


@never_cache
@require_GET
def inicio_continuacion_view(request):
    if not _invitacion_de_sesion(request):
        return _render_invitacion_invalida(request)
    if request.user.is_authenticated:
        return redirect('financiacion_educativa_web:confirmar')
    return render(
        request,
        'financiacion_educativa/inicio_continuacion.html',
        {
            'login_url': reverse('financiacion_educativa_web:acceso'),
            'register_url': reverse('financiacion_educativa_web:registro'),
        },
    )


@never_cache
@require_http_methods(['GET', 'POST'])
def acceso_view(request):
    if not _invitacion_de_sesion(request):
        return _render_invitacion_invalida(request)
    if request.user.is_authenticated:
        return redirect('financiacion_educativa_web:confirmar')

    form = AccesoFinanciacionForm(request=request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        auth_login(request, form.get_user())
        return redirect('financiacion_educativa_web:confirmar')
    return render(
        request,
        'financiacion_educativa/acceso.html',
        {
            'form': form,
            'register_url': reverse('financiacion_educativa_web:registro'),
        },
    )


@never_cache
@require_http_methods(['GET', 'POST'])
def registro_view(request):
    if not _invitacion_de_sesion(request):
        return _render_invitacion_invalida(request)
    if request.user.is_authenticated:
        return redirect('financiacion_educativa_web:confirmar')

    form = RegistroFinanciacionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                usuario = form.save()
        except IntegrityError:
            form.add_error(
                'email',
                'No fue posible crear la cuenta. Inicia sesion o recupera tu acceso.',
            )
        else:
            auth_login(
                request,
                usuario,
                backend='django.contrib.auth.backends.ModelBackend',
            )
            return redirect('financiacion_educativa_web:confirmar')
    return render(
        request,
        'financiacion_educativa/registro.html',
        {
            'form': form,
            'login_url': reverse('financiacion_educativa_web:acceso'),
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def confirmar_asociacion_view(request):
    invitacion = _invitacion_de_sesion(request)
    if not invitacion:
        return _render_invitacion_invalida(request)

    if request.method == 'POST':
        try:
            resultado = asociar_usuario_mediante_invitacion(
                invitacion_id=invitacion.pk,
                usuario=request.user,
            )
        except InvitacionNoValida:
            return _render_invitacion_invalida(request)
        request.session.pop(SESSION_INVITACION_ID, None)
        return redirect(
            'financiacion_educativa_web:terminos',
            solicitud_id=resultado.solicitud.pk,
        )

    return render(
        request,
        'financiacion_educativa/confirmar.html',
        {'solicitud': invitacion.solicitud},
    )


def _solicitud_del_usuario(request, solicitud_id):
    return get_object_or_404(
        SolicitudFinanciacionEducativa,
        pk=solicitud_id,
        usuario=request.user,
    )


def _session_terminos_key(solicitud_id):
    return f'financiacion_educativa_terminos_{solicitud_id}'


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def terminos_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        return redirect(
            'financiacion_educativa_web:siguiente',
            solicitud_id=solicitud.pk,
        )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_TERMS:
        raise Http404

    versiones = obtener_versiones_terminos_vigentes(obligatorios=True)
    session_key = _session_terminos_key(solicitud.pk)
    error = ''
    if request.method == 'GET':
        request.session[session_key] = [str(version.pk) for version in versiones]
    else:
        presentadas = set(request.session.get(session_key, []))
        aceptadas = set(request.POST.getlist('accepted_versions'))
        actuales = {str(version.pk) for version in versiones}
        if not actuales or aceptadas != presentadas or presentadas != actuales:
            error = (
                'Debes revisar y aceptar expresamente todos los terminos vigentes.'
            )
        else:
            versiones_aceptadas = list(
                VersionTerminosFinanciacion.objects.filter(pk__in=aceptadas)
            )
            try:
                resultado = aceptar_terminos_solicitud(
                    solicitud=solicitud,
                    usuario=request.user,
                    versiones=versiones_aceptadas,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                )
            except ValidationError:
                error = (
                    'Los terminos cambiaron o ya no estan vigentes. '
                    'Revisa nuevamente su contenido.'
                )
            else:
                request.session.pop(session_key, None)
                return redirect(
                    'financiacion_educativa_web:siguiente',
                    solicitud_id=resultado.solicitud.pk,
                )

    return render(
        request,
        'financiacion_educativa/terminos.html',
        {
            'solicitud': solicitud,
            'versiones': versiones,
            'error': error,
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def siguiente_paso_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        raise Http404
    return render(
        request,
        'financiacion_educativa/siguiente_paso.html',
        {'solicitud': solicitud},
    )
