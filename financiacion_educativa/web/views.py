import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoEnlaceCapturaMovil,
    EstadoEntregaCapturaMovil,
    EstadoSolicitudFinanciacion,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoEventoSeguridadFinanciacion,
    TipoArtefactoContractualEducativo,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    DocumentoFinanciacion,
    EnlaceCapturaMovil,
    EvidenciaMatricula,
    ParticipanteFinanciacion,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.asociacion import (
    asociar_usuario_mediante_invitacion,
)
from financiacion_educativa.services.autorizacion import (
    registrar_evento_seguridad,
    usuario_coincide_con_correo,
    usuario_es_propietario_solicitud,
)
from financiacion_educativa.services.captura_movil import (
    consumir_enlace_captura_movil,
    emitir_enlace_captura_movil,
    obtener_enlace_vigente_por_token,
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
from financiacion_educativa.services.ficha_matricula import (
    construir_mapeo_ficha_matricula,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    calcular_edad,
    fecha_referencia_solicitud,
    registrar_o_actualizar_participante,
    sincronizar_estudiante_desde_solicitud,
    solicitud_requiere_tutor,
)
from financiacion_educativa.services.politica_documental import (
    caras_identificacion_requeridas,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
    completar_fase_documental,
    reanudar_fase_documental_corregida,
)
from financiacion_educativa.services.progreso_publico import (
    obtener_progreso_publico,
)
from financiacion_educativa.services.proyecciones_financieras import (
    proyectar_abono_capital,
    proyectar_pago_total,
)
from financiacion_educativa.services.simulacion import (
    simular_financiacion_educativa,
)
from financiacion_educativa.services.simulador_publico import (
    limite_simulador_publico_excedido,
)
from financiacion_educativa.services.reanudacion import (
    resolver_destino_reanudacion,
    resolver_url_reanudacion,
)
from financiacion_educativa.services.estado_publico import (
    obtener_resultado_publico,
)
from financiacion_educativa.services.configuracion_financiera import (
    ConfiguracionFinancieraAmbigua,
    ConfiguracionFinancieraNoDisponible,
)
from .forms import (
    AccesoFinanciacionForm,
    EstudianteFinanciacionForm,
    DocumentoFinanciacionForm,
    EvidenciaMatriculaForm,
    RegistroFinanciacionForm,
    ReemplazoDocumentoForm,
    SimulacionFinanciacionEducativaForm,
    SimulacionPublicaFinanciacionEducativaForm,
    TutorFinanciacionForm,
    ProyeccionAbonoForm,
    ProyeccionPagoTotalForm,
)


SESSION_INVITACION_ID = 'financiacion_educativa_invitacion_id'
SESSION_ENLACE_CAPTURA_MOVIL_ID = (
    'financiacion_educativa_enlace_captura_movil_id'
)
SESSION_CAPTURA_MOVIL_GRANT = 'financiacion_educativa_captura_movil_grant'
SESSION_CONTEXTO_MOVIL_RECUPERADO = (
    'financiacion_educativa_contexto_movil_recuperado'
)
CONTEXTO_MOVIL_BOOTSTRAP_SALT = (
    'financiacion_educativa.contexto_movil_bootstrap'
)
CONTEXTO_MOVIL_BOOTSTRAP_SECONDS = 300
logger = logging.getLogger(__name__)
MIME_PREVISUALIZABLES = {
    'application/pdf',
    'image/jpeg',
    'image/png',
}
ORIGEN_CAPTURA_POR_METODO_CLIENTE = {
    'webrtc': OrigenCapturaDocumento.WEBRTC_CAMERA,
    'native': OrigenCapturaDocumento.NATIVE_CAMERA_FALLBACK,
}


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


def _render_enlace_captura_invalido(request):
    request.session.pop(SESSION_ENLACE_CAPTURA_MOVIL_ID, None)
    return _sin_referer(
        render(
            request,
            'financiacion_educativa/captura_movil_invalida.html',
            status=410,
        )
    )


def _endpoint_actual(request):
    resolver_match = getattr(request, 'resolver_match', None)
    return getattr(resolver_match, 'view_name', '') or 'unknown'


def _registrar_evento_seguro(request, *, tipo, solicitud=None, actor=None):
    try:
        registrar_evento_seguridad(
            tipo=tipo,
            endpoint=_endpoint_actual(request),
            solicitud=solicitud,
            actor=actor if actor is not None else request.user,
            metodo=request.method,
        )
    except Exception:
        logger.exception('No fue posible registrar un evento de seguridad.')


def _render_cuenta_no_coincide(request, solicitud):
    request.session.pop(SESSION_INVITACION_ID, None)
    _registrar_evento_seguro(
        request,
        tipo=TipoEventoSeguridadFinanciacion.INVITATION_ACCOUNT_MISMATCH,
        solicitud=solicitud,
    )
    return _sin_referer(
        render(
            request,
            'financiacion_educativa/cuenta_no_coincide.html',
            status=404,
        )
    )


def _es_contexto_movil(request):
    client_hint = request.headers.get('Sec-CH-UA-Mobile', '').strip()
    if client_hint == '?1':
        return True
    user_agent = request.headers.get('User-Agent', '').casefold()
    indicadores = (
        'android',
        'iphone',
        'ipad',
        'ipod',
        'mobile',
        'windows phone',
    )
    if any(indicador in user_agent for indicador in indicadores):
        return True
    expiracion = request.session.get(SESSION_CONTEXTO_MOVIL_RECUPERADO)
    if (
        isinstance(expiracion, (int, float))
        and not isinstance(expiracion, bool)
        and expiracion > timezone.now().timestamp()
    ):
        return True
    request.session.pop(SESSION_CONTEXTO_MOVIL_RECUPERADO, None)
    return False


def _crear_marcador_bootstrap_movil():
    expira_en = timezone.now() + timedelta(
        seconds=CONTEXTO_MOVIL_BOOTSTRAP_SECONDS
    )
    return signing.dumps(
        {
            'purpose': 'apple-touch-mobile-recovery',
            'expires_at': int(expira_en.timestamp()),
        },
        salt=CONTEXTO_MOVIL_BOOTSTRAP_SALT,
        compress=True,
    )


def _confirmar_contexto_movil_recuperado(request):
    if request.POST.get('mobile_context_kind') != 'apple-touch':
        return False
    user_agent = request.headers.get('User-Agent', '').casefold()
    if 'macintosh' not in user_agent and 'mac os x' not in user_agent:
        return False
    try:
        payload = signing.loads(
            request.POST.get('mobile_context_bootstrap', ''),
            salt=CONTEXTO_MOVIL_BOOTSTRAP_SALT,
        )
    except (signing.BadSignature, TypeError, ValueError):
        return False
    expiracion = payload.get('expires_at') if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get('purpose') != 'apple-touch-mobile-recovery'
        or not isinstance(expiracion, int)
        or isinstance(expiracion, bool)
        or expiracion <= int(timezone.now().timestamp())
    ):
        return False
    minutos = max(
        1,
        settings.FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TTL_MINUTES,
    )
    request.session[SESSION_CONTEXTO_MOVIL_RECUPERADO] = int(
        (timezone.now() + timedelta(minutes=minutos)).timestamp()
    )
    return True


def _render_requiere_dispositivo_movil(request):
    request.session.pop(SESSION_ENLACE_CAPTURA_MOVIL_ID, None)
    return _sin_referer(
        render(
            request,
            'financiacion_educativa/captura_movil_requerida.html',
            status=400,
        )
    )


def _invitacion_de_sesion(request):
    return obtener_invitacion_vigente_por_id(
        request.session.get(SESSION_INVITACION_ID)
    )


def _usuario_puede_usar_invitacion(usuario, invitacion):
    solicitud = invitacion.solicitud
    return bool(
        usuario_coincide_con_correo(usuario, solicitud.correo)
        and (
            solicitud.usuario_id is None
            or solicitud.usuario_id == usuario.pk
        )
    )


@never_cache
@require_GET
def continuar_invitacion_view(request, token):
    invitacion = obtener_invitacion_vigente_por_token(token)
    if not invitacion:
        return _render_invitacion_invalida(request)
    if (
        request.user.is_authenticated
        and not _usuario_puede_usar_invitacion(request.user, invitacion)
    ):
        return _render_cuenta_no_coincide(request, invitacion.solicitud)

    request.session[SESSION_INVITACION_ID] = str(invitacion.pk)
    return _sin_referer(
        redirect('financiacion_educativa_web:inicio')
    )


@never_cache
@require_GET
def inicio_continuacion_view(request):
    invitacion = _invitacion_de_sesion(request)
    if not invitacion:
        return _render_invitacion_invalida(request)
    if request.user.is_authenticated:
        if not _usuario_puede_usar_invitacion(request.user, invitacion):
            return _render_cuenta_no_coincide(request, invitacion.solicitud)
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
    invitacion = _invitacion_de_sesion(request)
    if not invitacion:
        return _render_invitacion_invalida(request)
    if request.user.is_authenticated:
        return redirect('financiacion_educativa_web:confirmar')

    form = AccesoFinanciacionForm(request=request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.get_user()
        if not _usuario_puede_usar_invitacion(usuario, invitacion):
            _registrar_evento_seguro(
                request,
                tipo=(
                    TipoEventoSeguridadFinanciacion
                    .INVITATION_ACCOUNT_MISMATCH
                ),
                solicitud=invitacion.solicitud,
                actor=usuario,
            )
            form.add_error(
                None,
                'Usa la cuenta correspondiente al correo que recibio la invitacion.',
            )
        else:
            auth_login(request, usuario)
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
    invitacion = _invitacion_de_sesion(request)
    if not invitacion:
        return _render_invitacion_invalida(request)
    if request.user.is_authenticated:
        return redirect('financiacion_educativa_web:confirmar')

    form = RegistroFinanciacionForm(
        request.POST or None,
        expected_email=invitacion.solicitud.correo,
    )
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
    if not _usuario_puede_usar_invitacion(request.user, invitacion):
        return _render_cuenta_no_coincide(request, invitacion.solicitud)

    if request.method == 'POST':
        try:
            resultado = asociar_usuario_mediante_invitacion(
                invitacion_id=invitacion.pk,
                usuario=request.user,
            )
        except InvitacionNoValida:
            _registrar_evento_seguro(
                request,
                tipo=TipoEventoSeguridadFinanciacion.REASSOCIATION_ATTEMPT,
                solicitud=invitacion.solicitud,
            )
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


def _solicitud_del_usuario(request, solicitud_id, *, permitir_revisor=False):
    solicitud = get_object_or_404(
        SolicitudFinanciacionEducativa,
        pk=solicitud_id,
    )
    if usuario_es_propietario_solicitud(request.user, solicitud):
        return solicitud
    if (
        permitir_revisor
        and request.user.is_authenticated
        and request.user.has_perm(
            'financiacion_educativa.revisar_solicitud_financiacion'
        )
    ):
        return solicitud
    _registrar_evento_seguro(
        request,
        tipo=TipoEventoSeguridadFinanciacion.UNAUTHORIZED_APPLICATION_ACCESS,
        solicitud=solicitud,
    )
    raise Http404


def _estado_documental_editable(solicitud):
    return solicitud.estado in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.PENDING_GUARDIAN,
        EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
    }


def _reanudar_correccion_completa(*, solicitud, actor):
    try:
        return reanudar_fase_documental_corregida(
            solicitud=solicitud,
            actor=actor,
        )
    except ValidationError:
        return False


def _destino_documental(solicitud, reanudada):
    nombre = 'procesamiento' if reanudada else 'documentacion'
    return reverse(
        f'financiacion_educativa_web:{nombre}',
        kwargs={'solicitud_id': solicitud.pk},
    )


def _captura_movil_autorizada(request, solicitud, persona):
    grant = request.session.get(SESSION_CAPTURA_MOVIL_GRANT)
    if not isinstance(grant, dict) or not _es_contexto_movil(request):
        return False
    if (
        grant.get('solicitud_id') != str(solicitud.pk)
        or grant.get('persona') != persona
    ):
        return False
    return EnlaceCapturaMovil.objects.filter(
        pk=grant.get('enlace_id'),
        solicitud=solicitud,
        persona=TIPOS_IDENTIDAD_POR_PERSONA.get(persona, {}).get('rol'),
        estado=EstadoEnlaceCapturaMovil.CONSUMED,
        consumida_por=request.user,
        vence_en__gt=timezone.now(),
    ).exists()


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
                sincronizar_estudiante_desde_solicitud(
                    solicitud=resultado.solicitud,
                    actor=request.user,
                )
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
def estado_procesamiento_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    respuesta = JsonResponse(obtener_progreso_publico(solicitud))
    respuesta['Cache-Control'] = 'private, no-store, max-age=0'
    respuesta['Pragma'] = 'no-cache'
    return respuesta


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def procesamiento_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    respuesta = render(
        request,
        'financiacion_educativa/procesamiento.html',
        {
            'solicitud': solicitud,
            'progreso': obtener_progreso_publico(solicitud),
            'estado_url': reverse(
                'financiacion_educativa_web:estado-procesamiento',
                kwargs={'solicitud_id': solicitud.pk},
            ),
        },
    )
    respuesta['Cache-Control'] = 'private, no-store, max-age=0'
    respuesta['Pragma'] = 'no-cache'
    return respuesta


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def documentacion_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado not in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.PENDING_GUARDIAN,
        EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        EstadoSolicitudFinanciacion.APPROVED,
        EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
        EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        EstadoSolicitudFinanciacion.ACTIVE,
        EstadoSolicitudFinanciacion.REJECTED,
    }:
        raise Http404
    try:
        matricula = solicitud.evidencia_matricula
    except EvidenciaMatricula.DoesNotExist:
        matricula = None
    participantes = solicitud.participantes.prefetch_related('roles')
    estudiante_asignado = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=RolParticipante.STUDENT).first()
    tutor_asignado = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=RolParticipante.GUARDIAN).first()
    deudor_asignado = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=RolParticipante.PRINCIPAL_DEBTOR).first()
    estudiante = (
        estudiante_asignado.participante if estudiante_asignado else None
    )
    requiere_tutor = solicitud_requiere_tutor(solicitud)
    requisitos = calcular_requisitos_documentales(solicitud)
    ultima_decision = solicitud.decisiones_revision.order_by(
        '-creada_en',
        '-id',
    ).first()
    return render(
        request,
        'financiacion_educativa/documentacion.html',
        {
            'solicitud': solicitud,
            'participantes': participantes,
            'estudiante': estudiante,
            'tutor': tutor_asignado.participante if tutor_asignado else None,
            'deudor': deudor_asignado.participante if deudor_asignado else None,
            'requiere_tutor': requiere_tutor,
            'documentos': solicitud.documentos.filter(activo=True).select_related(
                'participante'
            ),
            'matricula': matricula,
            'fotografia': _fotografia_activa(solicitud),
            'requisitos': requisitos,
            'pendientes': [
                requisito for requisito in requisitos if not requisito.cumplido
            ],
            'documental_editable': _estado_documental_editable(solicitud),
            'ultima_decision': ultima_decision,
            'artefactos_contractuales': (
                solicitud.artefactos_contractuales.filter(
                    vigente=True,
                ).order_by('tipo')
            ),
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def participante_view(request, solicitud_id, participante_id=None):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if not _estado_documental_editable(solicitud):
        raise Http404
    participante = None
    if participante_id:
        participante = get_object_or_404(
            ParticipanteFinanciacion,
            pk=participante_id,
            solicitud=solicitud,
        )
    roles_participante = set()
    if participante:
        roles_participante = set(
            participante.roles.values_list('rol', flat=True)
        )

    tipo_persona = request.POST.get('tipo_persona') or request.GET.get(
        'tipo'
    )
    if participante:
        tipo_persona = (
            'estudiante'
            if RolParticipante.STUDENT in roles_participante
            else 'tutor'
        )
    elif tipo_persona not in {'estudiante', 'tutor'}:
        tiene_estudiante = solicitud.roles_participantes.filter(
            rol=RolParticipante.STUDENT
        ).exists()
        if tiene_estudiante and solicitud_requiere_tutor(solicitud):
            tipo_persona = 'tutor'
        elif tiene_estudiante:
            raise Http404
        else:
            tipo_persona = 'estudiante'

    if tipo_persona == 'tutor':
        tiene_estudiante = solicitud.roles_participantes.filter(
            rol=RolParticipante.STUDENT
        ).exists()
        if not tiene_estudiante or not solicitud_requiere_tutor(solicitud):
            raise Http404

    if tipo_persona == 'estudiante':
        initial = {
            'tipo_documento': participante.tipo_documento,
            'numero_documento': participante.numero_documento,
            'pais_expedicion': participante.pais_expedicion,
            'fecha_nacimiento': participante.fecha_nacimiento,
        } if participante else None
        form = EstudianteFinanciacionForm(request.POST or None, initial=initial)
    else:
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
        } if participante else None
        form = TutorFinanciacionForm(request.POST or None, initial=initial)

    if request.method == 'POST' and form.is_valid():
        es_estudiante = tipo_persona == 'estudiante'
        if es_estudiante:
            nombres = solicitud.nombres
            apellidos = solicitud.apellidos
            correo = solicitud.correo
            telefono = solicitud.celular
            relacion = RelacionEstudiante.SELF
            roles = {RolParticipante.STUDENT}
            edad = calcular_edad(
                form.cleaned_data['fecha_nacimiento'],
                fecha_referencia_solicitud(solicitud),
            )
            if (
                edad is not None
                and edad >= settings.FINANCIACION_EDUCATIVA_MAYORIA_EDAD
            ):
                roles.add(RolParticipante.PRINCIPAL_DEBTOR)
        else:
            nombres = form.cleaned_data['nombres']
            apellidos = form.cleaned_data['apellidos']
            correo = form.cleaned_data['correo']
            telefono = form.cleaned_data['telefono']
            relacion = form.cleaned_data['relacion_estudiante']
            roles = {
                RolParticipante.GUARDIAN,
                RolParticipante.PRINCIPAL_DEBTOR,
            }
        datos = DatosParticipante(
            nombres=nombres,
            apellidos=apellidos,
            tipo_documento=form.cleaned_data['tipo_documento'],
            numero_documento=form.cleaned_data['numero_documento'],
            pais_expedicion=form.cleaned_data['pais_expedicion'],
            fecha_nacimiento=form.cleaned_data['fecha_nacimiento'],
            fecha_nacimiento_confirmada=False,
            correo=correo,
            telefono=telefono,
            relacion_estudiante=relacion,
        )
        try:
            registrar_o_actualizar_participante(
                solicitud=solicitud,
                actor=request.user,
                datos=datos,
                roles=roles,
                participante_id=getattr(participante, 'pk', None),
            )
        except ValidationError as error:
            _agregar_error_formulario(form, error)
        else:
            return redirect(
                _destino_documental(
                    solicitud,
                    _reanudar_correccion_completa(
                        solicitud=solicitud,
                        actor=request.user,
                    ),
                )
            )
    return render(
        request,
        'financiacion_educativa/participante_form.html',
        {
            'solicitud': solicitud,
            'participante': participante,
            'form': form,
            'tipo_persona': tipo_persona,
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def cargar_documento_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if not _estado_documental_editable(solicitud):
        raise Http404
    form = DocumentoFinanciacionForm(
        request.POST or None,
        request.FILES or None,
        solicitud=solicitud,
        tipo_inicial=request.GET.get('tipo', ''),
        participante_inicial=request.GET.get('participante', ''),
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
                _destino_documental(
                    solicitud,
                    _reanudar_correccion_completa(
                        solicitud=solicitud,
                        actor=request.user,
                    ),
                )
            )
    return render(
        request,
        'financiacion_educativa/documento_form.html',
        {'solicitud': solicitud, 'form': form, 'reemplazo': False},
    )


TIPOS_IDENTIDAD_POR_PERSONA = {
    'estudiante': {
        'rol': RolParticipante.STUDENT,
        'frente': TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
        'reverso': TipoDocumentoFinanciacion.STUDENT_ID_BACK,
    },
    'tutor': {
        'rol': RolParticipante.GUARDIAN,
        'frente': TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
        'reverso': TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
    },
}


def _error_json_validacion(error):
    if hasattr(error, 'message_dict'):
        mensajes = [
            str(mensaje)
            for lista in error.message_dict.values()
            for mensaje in lista
        ]
    else:
        mensajes = [str(mensaje) for mensaje in error.messages]
    return JsonResponse(
        {'ok': False, 'error': mensajes[0] if mensajes else 'Captura no valida.'},
        status=400,
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def capturar_identidad_view(request, solicitud_id, persona):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if not _estado_documental_editable(solicitud):
        raise Http404
    configuracion = TIPOS_IDENTIDAD_POR_PERSONA.get(persona)
    if not configuracion:
        raise Http404
    asignacion = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=configuracion['rol']).first()
    if not asignacion:
        raise Http404
    participante = asignacion.participante
    caras_requeridas = caras_identificacion_requeridas(
        participante.tipo_documento
    )
    tipos_requeridos = tuple(
        configuracion[cara] for cara in caras_requeridas
    )
    captura_movil_autorizada = _captura_movil_autorizada(
        request,
        solicitud,
        persona,
    )

    if request.method == 'POST':
        if not captura_movil_autorizada:
            _registrar_evento_seguro(
                request,
                tipo=(
                    TipoEventoSeguridadFinanciacion
                    .MOBILE_CAPTURE_CONTEXT_MISMATCH
                ),
                solicitud=solicitud,
            )
            raise Http404
        lado = request.POST.get('lado', '')
        if lado not in caras_requeridas:
            return JsonResponse(
                {'ok': False, 'error': 'La cara indicada no es requerida.'},
                status=400,
            )
        tipo = configuracion.get(lado)
        captura = request.FILES.get('captura')
        origen_captura = ORIGEN_CAPTURA_POR_METODO_CLIENTE.get(
            request.POST.get('metodo_captura', '')
        )
        if not tipo or not captura:
            return JsonResponse(
                {'ok': False, 'error': 'Indica el lado y realiza la captura.'},
                status=400,
            )
        if not origen_captura:
            return JsonResponse(
                {
                    'ok': False,
                    'error': 'La modalidad de captura no esta permitida.',
                },
                status=400,
            )
        existente = solicitud.documentos.filter(
            participante=participante,
            tipo=tipo,
            activo=True,
        ).first()
        if existente and request.POST.get('confirmar_reemplazo') != '1':
            return JsonResponse(
                {
                    'ok': False,
                    'error': (
                        'La captura ya existe. Confirma expresamente '
                        'que deseas reemplazarla.'
                    ),
                },
                status=409,
            )
        try:
            if existente:
                documento = reemplazar_documento(
                    documento=existente,
                    archivo=captura,
                    actor=request.user,
                    origen_captura=origen_captura,
                )
            else:
                documento = registrar_documento(
                    solicitud=solicitud,
                    participante=participante,
                    tipo=tipo,
                    origen_captura=origen_captura,
                    archivo=captura,
                    actor=request.user,
                )
        except ValidationError as error:
            return _error_json_validacion(error)
        if (
            solicitud.documentos.filter(
                participante=participante,
                tipo__in=tipos_requeridos,
                activo=True,
            ).values('tipo').distinct().count()
            == len(tipos_requeridos)
        ):
            request.session.pop(SESSION_CAPTURA_MOVIL_GRANT, None)
        reanudada = _reanudar_correccion_completa(
            solicitud=solicitud,
            actor=request.user,
        )
        return JsonResponse({
            'ok': True,
            'lado': lado,
            'documento_id': str(documento.pk),
            'estado': documento.get_estado_validacion_display(),
            'processing_url': (
                _destino_documental(solicitud, True) if reanudada else None
            ),
        })

    documentos = {
        documento.tipo: documento
        for documento in solicitud.documentos.filter(
            participante=participante,
            tipo__in=tipos_requeridos,
            activo=True,
        )
    }
    return render(
        request,
        'financiacion_educativa/captura_identidad.html',
        {
            'solicitud': solicitud,
            'participante': participante,
            'persona': persona,
            'documento_frente': documentos.get(configuracion['frente']),
            'documento_reverso': documentos.get(configuracion['reverso']),
            'requiere_reverso': 'reverso' in caras_requeridas,
            'captura_movil_autorizada': captura_movil_autorizada,
            'captura_min_ancho': (
                settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_WIDTH
            ),
            'captura_min_alto': (
                settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_HEIGHT
            ),
        },
    )


@never_cache
@login_required(login_url='/accounts/login/')
@require_POST
def enviar_enlace_captura_movil_view(request, solicitud_id, persona):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    try:
        resultado = emitir_enlace_captura_movil(
            solicitud=solicitud,
            persona=persona,
            actor=request.user,
        )
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(
            request,
            (
                'Programamos el envio al correo registrado. Revisa tambien '
                'la carpeta de correo no deseado. El enlace sera personal, '
                'temporal y de un solo uso.'
            ),
        )
    return redirect(
        'financiacion_educativa_web:capturar-identidad',
        solicitud_id=solicitud.pk,
        persona=persona,
    )


@never_cache
@sensitive_post_parameters('token')
@require_http_methods(['GET', 'POST'])
def captura_movil_token_view(request):
    if request.method == 'GET':
        return render(
            request,
            'financiacion_educativa/captura_movil_handoff.html',
            {'mobile_context_bootstrap': _crear_marcador_bootstrap_movil()},
        )
    if (
        not _es_contexto_movil(request)
        and not _confirmar_contexto_movil_recuperado(request)
    ):
        return _render_requiere_dispositivo_movil(request)
    token = request.POST.get('token', '')
    enlace = obtener_enlace_vigente_por_token(token)
    if not enlace:
        return _render_enlace_captura_invalido(request)
    request.session[SESSION_ENLACE_CAPTURA_MOVIL_ID] = str(enlace.pk)
    destino = reverse(
        'financiacion_educativa_web:captura-movil-continuar'
    )
    if request.user.is_authenticated:
        return _sin_referer(redirect(destino))
    login_url = f'{settings.LOGIN_URL}?{urlencode({"next": destino})}'
    return _sin_referer(redirect(login_url))


@never_cache
@login_required(login_url='/accounts/login/')
@require_GET
def captura_movil_continuar_view(request):
    if not _es_contexto_movil(request):
        return _render_requiere_dispositivo_movil(request)
    enlace_id = request.session.pop(
        SESSION_ENLACE_CAPTURA_MOVIL_ID,
        None,
    )
    resultado = consumir_enlace_captura_movil(
        enlace_id=enlace_id,
        usuario=request.user,
    )
    if not resultado:
        return _render_enlace_captura_invalido(request)
    enlace, persona = resultado
    request.session[SESSION_CAPTURA_MOVIL_GRANT] = {
        'enlace_id': str(enlace.pk),
        'solicitud_id': str(enlace.solicitud_id),
        'persona': persona,
    }
    return _sin_referer(
        redirect(
            'financiacion_educativa_web:capturar-identidad',
            solicitud_id=enlace.solicitud_id,
            persona=persona,
        )
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def reemplazar_documento_view(request, solicitud_id, documento_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if not _estado_documental_editable(solicitud):
        raise Http404
    documento = get_object_or_404(
        DocumentoFinanciacion,
        pk=documento_id,
        solicitud=solicitud,
        activo=True,
    )
    if documento.tipo in {
        TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
        TipoDocumentoFinanciacion.STUDENT_ID_BACK,
        TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
        TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
    }:
        raise Http404
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
                _destino_documental(
                    solicitud,
                    _reanudar_correccion_completa(
                        solicitud=solicitud,
                        actor=request.user,
                    ),
                )
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
    solicitud = _solicitud_del_usuario(
        request,
        solicitud_id,
        permitir_revisor=True,
    )
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
@require_GET
def previsualizar_documento_view(request, solicitud_id, documento_id):
    solicitud = _solicitud_del_usuario(
        request,
        solicitud_id,
        permitir_revisor=True,
    )
    documento = get_object_or_404(
        DocumentoFinanciacion,
        pk=documento_id,
        solicitud=solicitud,
        activo=True,
    )
    if (
        not documento.archivo
        or documento.content_type not in MIME_PREVISUALIZABLES
    ):
        raise Http404
    respuesta = FileResponse(
        documento.archivo.open('rb'),
        as_attachment=False,
        filename=documento.nombre_original or 'documento',
        content_type=documento.content_type,
    )
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['X-Frame-Options'] = 'SAMEORIGIN'
    respuesta['Content-Security-Policy'] = (
        "default-src 'none'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'self'"
    )
    respuesta['Cross-Origin-Resource-Policy'] = 'same-origin'
    respuesta['Referrer-Policy'] = 'no-referrer'
    return respuesta


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['GET', 'POST'])
def matricula_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if not _estado_documental_editable(solicitud):
        raise Http404
    evidencia = EvidenciaMatricula.objects.filter(solicitud=solicitud).first()
    initial = None
    if evidencia:
        initial = {
            'periodo_academico': evidencia.periodo_academico,
            'referencia_matricula': evidencia.referencia_matricula,
        }
    form = EvidenciaMatriculaForm(
        request.POST or None,
        request.FILES or None,
        initial=initial,
        periodo_institucional=solicitud.periodo_academico,
        codigo_institucional=solicitud.codigo_matricula,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            registrar_o_actualizar_evidencia_matricula(
                solicitud=solicitud,
                actor=request.user,
                institucion_declarada=solicitud.institucion.nombre_comercial,
                programa_curso=solicitud.nombre_curso,
                periodo_academico=(
                    solicitud.periodo_academico
                    or form.cleaned_data['periodo_academico']
                ),
                referencia_matricula=(
                    solicitud.codigo_matricula
                    or form.cleaned_data['referencia_matricula']
                ),
                archivo=form.cleaned_data['archivo'],
            )
        except ValidationError as error:
            _agregar_error_formulario(form, error)
        else:
            return redirect(
                _destino_documental(
                    solicitud,
                    _reanudar_correccion_completa(
                        solicitud=solicitud,
                        actor=request.user,
                    ),
                )
            )
    return render(
        request,
        'financiacion_educativa/matricula_form.html',
        {'solicitud': solicitud, 'evidencia': evidencia, 'form': form},
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def ficha_matricula_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    if solicitud.estado not in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        EstadoSolicitudFinanciacion.APPROVED,
        EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
        EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        EstadoSolicitudFinanciacion.ACTIVE,
        EstadoSolicitudFinanciacion.REJECTED,
    }:
        raise Http404
    return _sin_referer(
        render(
            request,
            'financiacion_educativa/ficha_matricula.html',
            {
                'solicitud': solicitud,
                'mapeo_ficha': construir_mapeo_ficha_matricula(solicitud),
                'ficha_generada': (
                    ArtefactoContractualEducativo.objects.filter(
                        solicitud=solicitud,
                        tipo=(
                            TipoArtefactoContractualEducativo.ENROLLMENT_FORM
                        ),
                        vigente=True,
                    ).first()
                ),
            },
        )
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def descargar_artefacto_contractual_view(
    request,
    solicitud_id,
    artefacto_id,
):
    solicitud = _solicitud_del_usuario(
        request,
        solicitud_id,
        permitir_revisor=True,
    )
    artefacto = get_object_or_404(
        ArtefactoContractualEducativo,
        pk=artefacto_id,
        solicitud=solicitud,
        vigente=True,
    )
    if not artefacto.archivo:
        raise Http404
    respuesta = FileResponse(
        artefacto.archivo.open('rb'),
        as_attachment=True,
        filename=f'{artefacto.numero_documento}.pdf',
        content_type='application/pdf',
    )
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['Cross-Origin-Resource-Policy'] = 'same-origin'
    respuesta['Referrer-Policy'] = 'no-referrer'
    return respuesta


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def descargar_artefacto_firmado_view(
    request,
    solicitud_id,
    artefacto_id,
):
    solicitud = _solicitud_del_usuario(
        request,
        solicitud_id,
        permitir_revisor=True,
    )
    artefacto = get_object_or_404(
        ArtefactoContractualEducativo,
        pk=artefacto_id,
        solicitud=solicitud,
        vigente=True,
        estado=EstadoArtefactoContractualEducativo.SIGNED,
    )
    if not artefacto.archivo_firmado:
        raise Http404
    respuesta = FileResponse(
        artefacto.archivo_firmado.open('rb'),
        as_attachment=True,
        filename=f'{artefacto.numero_documento}-firmado.pdf',
        content_type='application/pdf',
    )
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['Cross-Origin-Resource-Policy'] = 'same-origin'
    respuesta['Referrer-Policy'] = 'no-referrer'
    return respuesta


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['POST'])
def completar_documentacion_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    enviada = False
    try:
        resultado = completar_fase_documental(
            solicitud=solicitud,
            actor=request.user,
        )
    except ValidationError:
        pendientes = [
            requisito
            for requisito in calcular_requisitos_documentales(solicitud)
            if not requisito.cumplido
        ]
        detalle = '; '.join(
            requisito.descripcion for requisito in pendientes
        )
        messages.error(
            request,
            (
                f'No fue posible enviar el expediente. '
                f'Pendientes: {detalle}.'
            ),
        )
    else:
        enviada = True
        messages.success(
            request,
            (
                (
                    'Expediente recibido. La validacion automatica fue iniciada.'
                    if settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED
                    else 'Expediente enviado a revision correctamente.'
                )
                if resultado.estado
                == EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
                else 'El expediente ya habia sido enviado a revision.'
            ),
        )
    return redirect(
        _destino_documental(solicitud, enviada)
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
        'abono_form': ProyeccionAbonoForm(),
        'pago_total_form': ProyeccionPagoTotalForm(solicitud=solicitud),
        **extra,
    }


def _serializar_simulacion(simulacion):
    resultado = simulacion.resultado
    configuracion = simulacion.configuracion
    return {
        'monto_solicitado': format(resultado.monto_solicitado, 'f'),
        'plazo_meses': resultado.plazo_meses,
        'valor_originacion': format(resultado.valor_originacion, 'f'),
        'valor_iva_originacion': format(resultado.valor_iva_originacion, 'f'),
        'valor_fondo_garantias': format(
            resultado.valor_fondo_garantias,
            'f',
        ),
        'valor_seguro_vida': format(resultado.valor_seguro_vida, 'f'),
        'capital_total_financiado': format(
            resultado.capital_total_financiado,
            'f',
        ),
        'intereses_totales': format(resultado.intereses_totales, 'f'),
        'cuota_informativa': format(resultado.cuota_informativa, 'f'),
        'total_proyectado': format(resultado.total_proyectado, 'f'),
        'moneda': configuracion.moneda,
        'codigo_configuracion': configuracion.codigo,
        'version_configuracion': configuracion.version,
        'tasa_interes_mensual': format(
            configuracion.tasa_interes_mensual,
            'f',
        ),
        'porcentaje_originacion': format(
            configuracion.porcentaje_originacion,
            'f',
        ),
        'porcentaje_iva_originacion': format(
            configuracion.porcentaje_iva_originacion,
            'f',
        ),
        'porcentaje_fondo_garantias': format(
            configuracion.porcentaje_fondo_garantias,
            'f',
        ),
        'porcentaje_seguro_vida': format(
            configuracion.porcentaje_seguro_vida,
            'f',
        ),
        'proveedor_fondo_garantias': configuracion.proveedor_fondo_garantias,
        'proveedor_seguro_vida': configuracion.proveedor_seguro_vida,
        'metodo_calculo': configuracion.metodo_calculo,
        'metodo_calculo_nombre': configuracion.get_metodo_calculo_display(),
        'plan': [
            {
                'numero': cuota.numero,
                'fecha_vencimiento': cuota.fecha_vencimiento.isoformat(),
                'saldo_inicial': format(cuota.saldo_inicial, 'f'),
                'interes': format(cuota.interes, 'f'),
                'capital': format(cuota.capital, 'f'),
                'valor_cuota': format(cuota.valor_cuota, 'f'),
                'saldo_final': format(cuota.saldo_final, 'f'),
            }
            for cuota in resultado.plan
        ],
    }


def _simulacion_desde_formulario(form):
    return simular_financiacion_educativa(
        monto_solicitado=form.cleaned_data['monto_solicitado'],
        plazo_meses=form.cleaned_data['plazo_meses'],
    )


def _respuesta_calculo_simulacion(form):
    if not form.is_valid():
        return JsonResponse(
            {
                'ok': False,
                'error': 'Revisa el monto y el plazo indicados.',
                'fields': {
                    campo: [str(mensaje) for mensaje in errores]
                    for campo, errores in form.errors.items()
                },
            },
            status=400,
        )
    try:
        simulacion = _simulacion_desde_formulario(form)
    except (ConfiguracionFinancieraNoDisponible, ConfiguracionFinancieraAmbigua):
        return JsonResponse(
            {
                'ok': False,
                'error': 'La politica financiera educativa no esta disponible.',
            },
            status=503,
        )
    except ValidationError:
        logger.exception('Fallo controlado al simular financiacion educativa.')
        return JsonResponse(
            {
                'ok': False,
                'error': 'No fue posible calcular el escenario solicitado.',
            },
            status=400,
        )
    return JsonResponse(
        {'ok': True, 'simulation': _serializar_simulacion(simulacion)}
    )


def _simulacion_inicial(form_class, datos):
    validacion = form_class(datos)
    if not validacion.is_valid():
        return None, 'Los datos iniciales no estan dentro del rango del simulador.'
    try:
        return _simulacion_desde_formulario(validacion), ''
    except ConfiguracionFinancieraNoDisponible:
        return None, 'No hay una politica financiera educativa activa.'
    except ConfiguracionFinancieraAmbigua:
        return None, 'La politica financiera educativa requiere revision.'
    except ValidationError:
        logger.exception('No fue posible generar la simulacion educativa inicial.')
        return None, 'No fue posible simular con los datos actuales.'


@never_cache
@require_GET
def simulador_publico_view(request):
    datos = {
        'monto_solicitado': (
            settings.FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_AMOUNT
        ),
        'plazo_meses': (
            settings.FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_TERM_MONTHS
        ),
    }
    form = SimulacionPublicaFinanciacionEducativaForm(initial=datos)
    simulacion, error = _simulacion_inicial(
        SimulacionPublicaFinanciacionEducativaForm,
        datos,
    )
    return render(
        request,
        'financiacion_educativa/simulador.html',
        {
            'form': form,
            'simulacion': simulacion,
            'error': error,
            'simulador_publico': True,
        },
    )


@never_cache
@sensitive_post_parameters('monto_solicitado')
@require_POST
def calcular_simulacion_publica_view(request):
    if limite_simulador_publico_excedido(request):
        response = JsonResponse(
            {
                'ok': False,
                'error': (
                    'Se alcanzo el limite temporal de simulaciones. '
                    'Intenta de nuevo en un momento.'
                ),
            },
            status=429,
        )
        response['Retry-After'] = str(
            settings.FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_WINDOW_SECONDS
        )
        return response
    return _respuesta_calculo_simulacion(
        SimulacionPublicaFinanciacionEducativaForm(request.POST)
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def simulador_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    datos_solicitud = {
        'monto_solicitado': solicitud.valor_plan,
        'plazo_meses': solicitud.plazo_meses,
    }
    form = SimulacionFinanciacionEducativaForm(initial=datos_solicitud)
    simulacion, error = _simulacion_inicial(
        SimulacionFinanciacionEducativaForm,
        datos_solicitud,
    )
    if error.startswith('Los datos iniciales'):
        error = 'Los datos institucionales no estan dentro del rango del simulador.'
    return render(
        request,
        'financiacion_educativa/simulador.html',
        {
            'solicitud': solicitud,
            'form': form,
            'simulacion': simulacion,
            'error': error,
        },
    )


@never_cache
@sensitive_post_parameters('monto_solicitado')
@login_required(login_url='/financiacion-educativa/acceso/')
@require_POST
def calcular_simulacion_view(request, solicitud_id):
    _solicitud_del_usuario(request, solicitud_id)
    return _respuesta_calculo_simulacion(
        SimulacionFinanciacionEducativaForm(request.POST)
    )


@never_cache
@login_required
@require_GET
def reanudar_solicitudes_view(request):
    solicitudes = list(
        SolicitudFinanciacionEducativa.objects.filter(
            usuario=request.user,
        ).select_related('institucion')
    )
    activas = [
        solicitud
        for solicitud in solicitudes
        if resolver_destino_reanudacion(solicitud).inconclusa
    ]
    if len(activas) == 1:
        return redirect(resolver_url_reanudacion(activas[0]))

    candidatas = activas or solicitudes
    if len(candidatas) == 1:
        return redirect(resolver_url_reanudacion(candidatas[0]))

    opciones = [
        {
            'solicitud': solicitud,
            'destino': resolver_destino_reanudacion(solicitud),
            'url': resolver_url_reanudacion(solicitud),
        }
        for solicitud in candidatas
    ]
    return render(
        request,
        'financiacion_educativa/mis_solicitudes.html',
        {'opciones': opciones},
    )


@never_cache
@login_required
@require_GET
def estado_solicitud_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    return render(
        request,
        'financiacion_educativa/estado_solicitud.html',
        {
            'solicitud': solicitud,
            'destino': resolver_destino_reanudacion(solicitud),
            'resultado_publico': obtener_resultado_publico(solicitud),
        },
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_GET
def finanzas_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    return render(
        request,
        'financiacion_educativa/finanzas.html',
        _contexto_financiero(solicitud),
    )


@never_cache
@login_required(login_url='/financiacion-educativa/acceso/')
@require_http_methods(['POST'])
def proyectar_abono_view(request, solicitud_id):
    solicitud = _solicitud_del_usuario(request, solicitud_id)
    fotografia = _fotografia_activa(solicitud)
    if not fotografia:
        raise Http404
    form = ProyeccionAbonoForm(request.POST)
    resultado = None
    if form.is_valid():
        try:
            resultado = proyectar_abono_capital(
                fotografia=fotografia,
                valor_pago=form.cleaned_data['valor_pago'],
                fecha_efectiva=form.cleaned_data['fecha_efectiva'],
            )
        except ValidationError as exc:
            _agregar_error_formulario(form, exc)
            messages.error(
                request,
                'No fue posible calcular la proyeccion. Revisa los campos marcados.',
            )
        else:
            messages.success(
                request,
                (
                    'Proyeccion calculada. Es informativa y no registra '
                    'ningun pago ni movimiento.'
                ),
            )
    else:
        messages.error(
            request,
            'Revisa los campos marcados para calcular la proyeccion.',
        )
    return render(
        request,
        'financiacion_educativa/finanzas.html',
        _contexto_financiero(
            solicitud,
            abono_form=form,
            proyeccion_abono=resultado,
            focus_projections=True,
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
            messages.error(
                request,
                'No fue posible calcular la liquidacion. Revisa los campos marcados.',
            )
        else:
            messages.success(
                request,
                (
                    'Liquidacion informativa calculada. No registra '
                    'ningun pago ni cancela la obligacion.'
                ),
            )
    else:
        messages.error(
            request,
            'Revisa los campos marcados para calcular la liquidacion.',
        )
    return render(
        request,
        'financiacion_educativa/finanzas.html',
        _contexto_financiero(
            solicitud,
            pago_total_form=form,
            proyeccion_pago_total=resultado,
            focus_projections=True,
        ),
    )
