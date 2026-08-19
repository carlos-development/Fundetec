from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AceptarDocumentoOperativoForm,
    CorreccionDocumentoOperativoForm,
    FiltrosRevisionDocumentalForm,
    FiltrosSolicitudesOperativasForm,
)
from .permissions import (
    PERMISO_DOCUMENTOS,
    PERMISO_PROCESOS,
    PERMISO_SOLICITUDES,
    PERMISO_ACCESO_REVISION_DOCUMENTAL,
    PERMISO_DECIDIR_REVISION_DOCUMENTAL,
    capacidades_operativas,
    requiere_permisos_operativos,
)
from .presenters import (
    presentar_detalle_solicitud,
    presentar_resumen_solicitud,
    presentar_documento_revision,
    presentar_resumen_documento_revision,
)
from .selectors import (
    filtrar_solicitudes_operativas,
    obtener_bandejas_operativas,
    obtener_distribucion_instituciones,
    obtener_indicadores_globales,
    obtener_instituciones_operativas,
    obtener_opciones_filtros,
    obtener_solicitud_operativa,
    obtener_solicitudes_recientes,
    filtrar_documentos_revision,
    obtener_documento_revision,
)
from financiacion_educativa.services.revision_documental_operativa import (
    ConflictoRevisionDocumental,
    aceptar_documento_operativo,
    documento_admite_revision,
    solicitar_correccion_documento_operativo,
)


MIME_PREVISUALIZABLES_OPERATIVOS = {
    'application/pdf': 'documento.pdf',
    'image/jpeg': 'documento.jpg',
    'image/png': 'documento.png',
}


def _contexto(request, *, seccion='resumen', **adicional):
    return {
        'seccion_operativa': seccion,
        'capacidades_operativas': capacidades_operativas(request.user),
        **adicional,
    }


def _querystring_sin_pagina(request):
    parametros = request.GET.copy()
    parametros.pop('page', None)
    return parametros.urlencode()


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def inicio_view(request):
    capacidades = capacidades_operativas(request.user)
    recientes = []
    if capacidades['solicitudes']:
        recientes = [
            presentar_resumen_solicitud(solicitud)
            for solicitud in obtener_solicitudes_recientes()
        ]
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/inicio.html',
        _contexto(
            request,
            indicadores=obtener_indicadores_globales(),
            bandejas=(
                obtener_bandejas_operativas()
                if capacidades['procesos']
                else []
            ),
            solicitudes_recientes=recientes,
            distribucion_instituciones=obtener_distribucion_instituciones(),
        ),
    )


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def solicitudes_view(request):
    formulario = FiltrosSolicitudesOperativasForm(
        request.GET,
        opciones=obtener_opciones_filtros(),
    )
    estado_http = 200
    if formulario.is_valid():
        consulta = filtrar_solicitudes_operativas(
            filtros=formulario.cleaned_data
        )
        pagina_solicitada = formulario.cleaned_data.get('page') or 1
    else:
        consulta = filtrar_solicitudes_operativas(filtros={}).none()
        pagina_solicitada = 1
        estado_http = 400

    paginador = Paginator(consulta, 25)
    try:
        pagina = paginador.page(pagina_solicitada)
    except (EmptyPage, PageNotAnInteger):
        pagina = paginador.get_page(1)
        formulario.add_error('page', 'La pagina solicitada no es valida.')
        estado_http = 400
    pagina.object_list = [
        presentar_resumen_solicitud(solicitud)
        for solicitud in pagina.object_list
    ]
    bandeja = (
        formulario.cleaned_data.get('bandeja')
        if formulario.is_valid()
        else ''
    )
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/solicitudes_lista.html',
        _contexto(
            request,
            seccion='firmas' if bandeja == 'firma_pendiente' else 'solicitudes',
            formulario_filtros=formulario,
            pagina=pagina,
            querystring=_querystring_sin_pagina(request),
            bandeja_activa=bandeja,
        ),
        status=estado_http,
    )


@never_cache
@requiere_permisos_operativos(PERMISO_PROCESOS)
@require_GET
def bandejas_view(request):
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/bandejas.html',
        _contexto(
            request,
            seccion='bandejas',
            bandejas=obtener_bandejas_operativas(),
        ),
    )


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def instituciones_view(request):
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/instituciones.html',
        _contexto(
            request,
            seccion='instituciones',
            instituciones=obtener_instituciones_operativas(),
        ),
    )


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def solicitud_detalle_view(request, application_id):
    solicitud = obtener_solicitud_operativa(application_id)
    capacidades = capacidades_operativas(request.user)
    detalle = presentar_detalle_solicitud(
        solicitud,
        datos_integrales=capacidades['datos_integrales'],
        puede_ver_documentos=(
            capacidades['documentos']
            and request.user.has_perm(PERMISO_DOCUMENTOS)
        ),
        puede_ver_procesos=(
            capacidades['procesos']
            and request.user.has_perm(PERMISO_PROCESOS)
        ),
    )
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/solicitud_detalle.html',
        _contexto(
            request,
            seccion='solicitudes',
            solicitud=detalle,
        ),
    )


@never_cache
@requiere_permisos_operativos(
    PERMISO_ACCESO_REVISION_DOCUMENTAL,
    PERMISO_DOCUMENTOS,
)
@require_GET
def revision_documental_view(request):
    instituciones = tuple(
        (str(pk), nombre)
        for pk, nombre in obtener_instituciones_operativas().values_list(
            'pk', 'nombre_comercial'
        )
    )
    formulario = FiltrosRevisionDocumentalForm(
        request.GET,
        instituciones=instituciones,
    )
    estado_http = 200
    if formulario.is_valid():
        consulta = filtrar_documentos_revision(
            filtros=formulario.cleaned_data
        )
        numero_pagina = formulario.cleaned_data.get('page') or 1
    else:
        consulta = filtrar_documentos_revision(filtros={}).none()
        numero_pagina = 1
        estado_http = 400
    paginador = Paginator(consulta, 25)
    try:
        pagina = paginador.page(numero_pagina)
    except (EmptyPage, PageNotAnInteger):
        pagina = paginador.get_page(1)
        estado_http = 400
    pagina.object_list = [
        presentar_resumen_documento_revision(documento)
        for documento in pagina.object_list
    ]
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/revision_documental_lista.html',
        _contexto(
            request,
            seccion='revision-documental',
            formulario_filtros=formulario,
            pagina=pagina,
            querystring=_querystring_sin_pagina(request),
        ),
        status=estado_http,
    )


def _respuesta_revision_documento(request, documento, *, status=200):
    detalle = presentar_documento_revision(documento)
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/revision_documental_detalle.html',
        _contexto(
            request,
            seccion='revision-documental',
            documento=detalle,
            admite_decision=documento_admite_revision(documento),
            puede_decidir=request.user.has_perm(
                PERMISO_DECIDIR_REVISION_DOCUMENTAL
            ),
            form_aceptar=AceptarDocumentoOperativoForm(),
            form_correccion=CorreccionDocumentoOperativoForm(),
        ),
        status=status,
    )


@never_cache
@requiere_permisos_operativos(
    PERMISO_ACCESO_REVISION_DOCUMENTAL,
    PERMISO_DOCUMENTOS,
)
@require_GET
def revision_documento_view(request, application_id):
    documento = obtener_documento_revision(application_id)
    return _respuesta_revision_documento(request, documento)


@never_cache
@requiere_permisos_operativos(
    PERMISO_ACCESO_REVISION_DOCUMENTAL,
    PERMISO_DOCUMENTOS,
)
@require_GET
def previsualizar_documento_operativo_view(request, application_id):
    documento = obtener_documento_revision(application_id)
    nombre = MIME_PREVISUALIZABLES_OPERATIVOS.get(documento.content_type)
    if not nombre or not documento.archivo:
        raise Http404
    respuesta = FileResponse(
        documento.archivo.open('rb'),
        as_attachment=False,
        filename=nombre,
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
    respuesta['Cache-Control'] = 'no-store, private'
    return respuesta


def _mensajes_validacion(error):
    if hasattr(error, 'message_dict'):
        return ' '.join(
            mensaje
            for mensajes in error.message_dict.values()
            for mensaje in mensajes
        )
    return ' '.join(error.messages)


@never_cache
@requiere_permisos_operativos(
    PERMISO_ACCESO_REVISION_DOCUMENTAL,
    PERMISO_DOCUMENTOS,
    PERMISO_DECIDIR_REVISION_DOCUMENTAL,
)
@require_POST
def aceptar_documento_view(request, application_id):
    formulario = AceptarDocumentoOperativoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, 'Revisa la observacion registrada.')
    else:
        try:
            resultado = aceptar_documento_operativo(
                documento_id=application_id,
                actor=request.user,
                observacion=formulario.cleaned_data['observacion'],
            )
        except ConflictoRevisionDocumental as error:
            messages.warning(request, _mensajes_validacion(error))
        except ValidationError as error:
            messages.error(request, _mensajes_validacion(error))
        else:
            messages.success(
                request,
                'La decision ya estaba registrada.'
                if resultado.repetida
                else 'Documento aceptado y decision auditada.',
            )
    return redirect(
        'financiacion_educativa_web:operaciones:revision-documento',
        application_id=application_id,
    )


@never_cache
@requiere_permisos_operativos(
    PERMISO_ACCESO_REVISION_DOCUMENTAL,
    PERMISO_DOCUMENTOS,
    PERMISO_DECIDIR_REVISION_DOCUMENTAL,
)
@require_POST
def solicitar_correccion_documento_view(request, application_id):
    formulario = CorreccionDocumentoOperativoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, 'Revisa los datos de la correccion.')
    else:
        try:
            resultado = solicitar_correccion_documento_operativo(
                documento_id=application_id,
                actor=request.user,
                **formulario.cleaned_data,
            )
        except ConflictoRevisionDocumental as error:
            messages.warning(request, _mensajes_validacion(error))
        except ValidationError as error:
            messages.error(request, _mensajes_validacion(error))
        else:
            messages.success(
                request,
                'La decision ya estaba registrada.'
                if resultado.repetida
                else 'Correccion solicitada mediante el outbox educativo.',
            )
    return redirect(
        'financiacion_educativa_web:operaciones:revision-documento',
        application_id=application_id,
    )
