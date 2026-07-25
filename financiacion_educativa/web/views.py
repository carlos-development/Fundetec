from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import (
    CondicionesFinancieras,
    DocumentoFinanciacion,
    EvidenciaMatricula,
    ParticipanteFinanciacion,
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
from financiacion_educativa.services.documentos import (
    registrar_documento,
    reemplazar_documento,
)
from financiacion_educativa.services.matricula import (
    registrar_o_actualizar_evidencia_matricula,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
    completar_fase_documental,
)
from financiacion_educativa.services.proyecciones_financieras import (
    proyectar_abono_capital,
    proyectar_pago_total,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.choices import OrigenCapturaDocumento

from .forms import (
    AccesoFinanciacionForm,
    DocumentoFinanciacionForm,
    EvidenciaMatriculaForm,
    CrearFotografiaFinancieraForm,
    ParticipanteFinanciacionForm,
    RegistroFinanciacionForm,
    ReemplazoDocumentoForm,
    ProyeccionAbonoForm,
    ProyeccionPagoTotalForm,
)


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


def _agregar_error_formulario(form, error):
    for mensaje in error.messages:
        form.add_error(None, mensaje)


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


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def documentacion_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado not in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
    }:
        raise Http404
    try:
        matricula = solicitud.evidencia_matricula
    except EvidenciaMatricula.DoesNotExist:
        matricula = None
    return render(
        request,
        'financiacion_educativa/documentacion.html',
        {
            'solicitud': solicitud,
            'participantes': solicitud.participantes.prefetch_related('roles'),
            'documentos': solicitud.documentos.filter(activo=True).select_related(
                'participante'
            ),
            'matricula': matricula,
            'requisitos': calcular_requisitos_documentales(solicitud),
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def participante_view(request, solicitud_id, participante_id=None):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        raise Http404
    participante = None
    if participante_id:
        participante = get_object_or_404(
            ParticipanteFinanciacion,
            pk=participante_id,
            solicitud=solicitud,
        )
    initial = None
    if participante:
        initial = {
            'nombres': participante.nombres,
            'apellidos': participante.apellidos,
            'tipo_documento': participante.tipo_documento,
            'numero_documento': participante.numero_documento,
            'pais_expedicion': participante.pais_expedicion,
            'fecha_nacimiento': participante.fecha_nacimiento,
            'correo': participante.correo,
            'telefono': participante.telefono,
            'relacion_estudiante': participante.relacion_estudiante,
            'roles': list(participante.roles.values_list('rol', flat=True)),
        }
    form = ParticipanteFinanciacionForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        datos = DatosParticipante(
            nombres=form.cleaned_data['nombres'],
            apellidos=form.cleaned_data['apellidos'],
            tipo_documento=form.cleaned_data['tipo_documento'],
            numero_documento=form.cleaned_data['numero_documento'],
            pais_expedicion=form.cleaned_data['pais_expedicion'],
            fecha_nacimiento=form.cleaned_data['fecha_nacimiento'],
            fecha_nacimiento_confirmada=False,
            correo=form.cleaned_data['correo'],
            telefono=form.cleaned_data['telefono'],
            relacion_estudiante=form.cleaned_data['relacion_estudiante'],
        )
        try:
            registrar_o_actualizar_participante(
                solicitud=solicitud,
                actor=request.user,
                datos=datos,
                roles=form.cleaned_data['roles'],
                participante_id=getattr(participante, 'pk', None),
            )
        except ValidationError as error:
            _agregar_error_formulario(form, error)
        else:
            return redirect(
                'financiacion_educativa_web:documentacion',
                solicitud_id=solicitud.pk,
            )
    return render(
        request,
        'financiacion_educativa/participante_form.html',
        {'solicitud': solicitud, 'participante': participante, 'form': form},
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def cargar_documento_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        raise Http404
    form = DocumentoFinanciacionForm(
        request.POST or None,
        request.FILES or None,
        solicitud=solicitud,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            registrar_documento(
                solicitud=solicitud,
                participante=form.cleaned_data['participante'],
                tipo=form.cleaned_data['tipo'],
                origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
                archivo=form.cleaned_data['archivo'],
                actor=request.user,
            )
        except ValidationError as error:
            _agregar_error_formulario(form, error)
        else:
            return redirect(
                'financiacion_educativa_web:documentacion',
                solicitud_id=solicitud.pk,
            )
    return render(
        request,
        'financiacion_educativa/documento_form.html',
        {'solicitud': solicitud, 'form': form, 'reemplazo': False},
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def reemplazar_documento_view(request, solicitud_id, documento_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        raise Http404
    documento = get_object_or_404(
        DocumentoFinanciacion,
        pk=documento_id,
        solicitud=solicitud,
        activo=True,
    )
    form = ReemplazoDocumentoForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            reemplazar_documento(
                documento=documento,
                archivo=form.cleaned_data['archivo'],
                actor=request.user,
            )
        except ValidationError as error:
            _agregar_error_formulario(form, error)
        else:
            return redirect(
                'financiacion_educativa_web:documentacion',
                solicitud_id=solicitud.pk,
            )
    return render(
        request,
        'financiacion_educativa/documento_form.html',
        {
            'solicitud': solicitud,
            'documento': documento,
            'form': form,
            'reemplazo': True,
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def descargar_documento_view(request, solicitud_id, documento_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    documento = get_object_or_404(
        DocumentoFinanciacion,
        pk=documento_id,
        solicitud=solicitud,
    )
    if not documento.archivo:
        raise Http404
    respuesta = FileResponse(
        documento.archivo.open('rb'),
        as_attachment=True,
        filename=documento.nombre_original or 'documento',
        content_type=documento.content_type or 'application/octet-stream',
    )
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['X-Frame-Options'] = 'DENY'
    respuesta['Content-Security-Policy'] = "sandbox; default-src 'none'"
    respuesta['Referrer-Policy'] = 'no-referrer'
    return respuesta


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def matricula_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        raise Http404
    evidencia = EvidenciaMatricula.objects.filter(solicitud=solicitud).first()
    initial = None
    if evidencia:
        initial = {
            'institucion_declarada': evidencia.institucion_declarada,
            'programa_curso': evidencia.programa_curso,
            'periodo_academico': evidencia.periodo_academico,
            'referencia_matricula': evidencia.referencia_matricula,
        }
    form = EvidenciaMatriculaForm(
        request.POST or None,
        request.FILES or None,
        initial=initial,
        requiere_archivo=evidencia is None,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            registrar_o_actualizar_evidencia_matricula(
                solicitud=solicitud,
                actor=request.user,
                institucion_declarada=form.cleaned_data['institucion_declarada'],
                programa_curso=form.cleaned_data['programa_curso'],
                periodo_academico=form.cleaned_data['periodo_academico'],
                referencia_matricula=form.cleaned_data['referencia_matricula'],
                archivo=form.cleaned_data['archivo'],
            )
        except ValidationError as error:
            _agregar_error_formulario(form, error)
        else:
            return redirect(
                'financiacion_educativa_web:documentacion',
                solicitud_id=solicitud.pk,
            )
    return render(
        request,
        'financiacion_educativa/matricula_form.html',
        {'solicitud': solicitud, 'evidencia': evidencia, 'form': form},
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['POST'])
def completar_documentacion_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    try:
        completar_fase_documental(solicitud=solicitud, actor=request.user)
    except ValidationError:
        pass
    return redirect(
        'financiacion_educativa_web:documentacion',
        solicitud_id=solicitud.pk,
    )


def _fotografia_activa(solicitud):
    return CondicionesFinancieras.objects.filter(
        solicitud=solicitud,
        activa=True,
        es_legado=False,
    ).prefetch_related('cuotas').first()


def _contexto_financiero(solicitud, **extra):
    fotografia = _fotografia_activa(solicitud)
    return {
        'solicitud': solicitud,
        'fotografia': fotografia,
        'crear_form': CrearFotografiaFinancieraForm(),
        'abono_form': ProyeccionAbonoForm(solicitud=solicitud),
        'pago_total_form': ProyeccionPagoTotalForm(solicitud=solicitud),
        **extra,
    }


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def finanzas_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    fotografia = _fotografia_activa(solicitud)
    crear_form = CrearFotografiaFinancieraForm(request.POST or None)
    error = ''
    if request.method == 'POST':
        if fotografia:
            return redirect(
                'financiacion_educativa_web:finanzas',
                solicitud_id=solicitud.pk,
            )
        if crear_form.is_valid():
            try:
                crear_fotografia_condiciones_financieras(
                    solicitud,
                    fecha_inicio_plan=crear_form.cleaned_data['fecha_inicio_plan'],
                    actor=request.user,
                )
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            else:
                return redirect(
                    'financiacion_educativa_web:finanzas',
                    solicitud_id=solicitud.pk,
                )
    contexto = _contexto_financiero(
        solicitud,
        crear_form=crear_form,
        error=error,
    )
    return render(
        request,
        'financiacion_educativa/finanzas.html',
        contexto,
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['POST'])
def proyectar_abono_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    fotografia = _fotografia_activa(solicitud)
    if not fotografia:
        raise Http404
    form = ProyeccionAbonoForm(request.POST, solicitud=solicitud)
    resultado = None
    if form.is_valid():
        try:
            resultado = proyectar_abono_capital(
                fotografia=fotografia,
                valor_pago=form.cleaned_data['valor_pago'],
                fecha_efectiva=form.cleaned_data['fecha_efectiva'],
                cuotas_cubiertas=form.cleaned_data['cuotas_cubiertas'],
                participante_pagante_id=getattr(
                    form.cleaned_data['participante_pagante'],
                    'pk',
                    None,
                ),
            )
        except ValidationError as exc:
            _agregar_error_formulario(form, exc)
    return render(
        request,
        'financiacion_educativa/finanzas.html',
        _contexto_financiero(
            solicitud,
            abono_form=form,
            proyeccion_abono=resultado,
        ),
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['POST'])
def proyectar_pago_total_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    fotografia = _fotografia_activa(solicitud)
    if not fotografia:
        raise Http404
    form = ProyeccionPagoTotalForm(request.POST, solicitud=solicitud)
    resultado = None
    if form.is_valid():
        try:
            resultado = proyectar_pago_total(
                fotografia=fotografia,
                fecha_efectiva=form.cleaned_data['fecha_efectiva'],
                cuotas_cubiertas=form.cleaned_data['cuotas_cubiertas'],
                participante_pagante_id=getattr(
                    form.cleaned_data['participante_pagante'],
                    'pk',
                    None,
                ),
            )
        except ValidationError as exc:
            _agregar_error_formulario(form, exc)
    return render(
        request,
        'financiacion_educativa/finanzas.html',
        _contexto_financiero(
            solicitud,
            pago_total_form=form,
            proyeccion_pago_total=resultado,
        ),
    )
