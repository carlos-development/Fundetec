import uuid
from io import BytesIO
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from financiacion_educativa.models_recaudos import (
    ArchivoRecaudoEducativo, ReporteRecaudoEducativo, ConciliacionRecaudoEducativo, ReceptorBrebEducativo,
)
from financiacion_educativa.services import aplicacion_cuotas
from financiacion_educativa.services import recaudos as servicios
from financiacion_educativa.dashboards.operaciones.permissions import requiere_permisos_operativos, capacidades_operativas
from .forms_recaudos import (
    ReportarPagoForm, ConfirmarRecaudoForm, EvidenciaBancariaForm, RechazarReporteForm, FiltrosRecaudosForm,
    AplicarCuotaForm,
    RegistrarPagoRecibidoForm,
)
from .portal_estudiante import calendario_del_portal


def _habilitado():
    if not settings.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED:
        raise Http404


def _propiedad(request, solicitud_id):
    _habilitado()
    try:
        return servicios.obligacion_del_usuario(solicitud_id=solicitud_id, actor=request.user)
    except PermissionDenied:
        raise Http404 from None


def _error(form, error):
    if hasattr(error, 'error_dict'):
        for campo, errores in error.error_dict.items():
            form.add_error(campo if campo in form.fields else None, errores)
    else:
        form.add_error(None, error)


def _reportes():
    from financiacion_educativa.services.libro_pagos import anotar_reportes
    return anotar_reportes(ReporteRecaudoEducativo.objects.select_related(
        'solicitud__institucion', 'receptor__cuenta', 'comprobante', 'conciliacion__aplicacion__cuota',
        'conciliacion__cuenta', 'reportado_por', 'rechazo',
    )).order_by('-creado_en', 'pk')


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def pagos_view(request, solicitud_id):
    fotografia = _propiedad(request, solicitud_id)
    receptor = servicios.receptor_vigente()
    pendiente = calendario_del_portal(fotografia)['primera_cuota_pendiente']
    form = ReportarPagoForm(request.POST if request.method == 'POST' else None, request.FILES if request.method == 'POST' else None, initial={
        'clave': uuid.uuid4(), 'receptor_id': receptor.pk if receptor else None,
        'monto': pendiente['valor_cuota'] if pendiente else None, 'fecha': timezone.localdate(),
    })
    if request.method == 'POST' and form.is_valid():
        try:
            reporte, replay = servicios.reportar_pago(solicitud_id=solicitud_id, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            _error(form, error)
        else:
            messages.success(request, 'Reporte recibido. La confirmacion requiere verificar el ingreso en el banco.' if not replay else 'Este reporte ya fue recibido; no se creo otro.')
            return redirect('financiacion_educativa_web:reporte-pago', solicitud_id=solicitud_id, reporte_id=reporte.pk)
    try:
        receptor_enviado = uuid.UUID(request.POST.get('receptor_id', ''))
    except (ValueError, TypeError, AttributeError):
        receptor_enviado = None
    receptor_cambio = request.method == 'POST' and receptor_enviado is not None and receptor_enviado != (receptor.pk if receptor else None)
    if receptor_cambio:
        receptor = None
    return render(request, 'financiacion_educativa/recaudos/reportar.html', {
        'fotografia': fotografia, 'solicitud': fotografia.solicitud, 'receptor': receptor, 'form': form,
        'reportes': _reportes().filter(solicitud_id=solicitud_id),
        'receptor_cambio': receptor_cambio,
    })


@never_cache
@login_required
@require_GET
def reporte_view(request, solicitud_id, reporte_id):
    fotografia = _propiedad(request, solicitud_id)
    reporte = get_object_or_404(_reportes(), pk=reporte_id, solicitud_id=solicitud_id)
    return render(request, 'financiacion_educativa/recaudos/estado.html', {'reporte': reporte, 'solicitud': fotografia.solicitud})


def _archivo_response(archivo, descargar=False):
    if not archivo or archivo.estado_escaneo != 'SAFE':
        raise Http404('El soporte todavia no esta disponible para consulta.')
    extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'application/pdf': 'pdf'}[archivo.content_type]
    try:
        contenido = servicios.leer_soporte_seguro(archivo)
    except ValidationError:
        raise Http404('Soporte no disponible.') from None
    # Se sirve el mismo contenido verificado, sin reabrir un archivo mutable.
    stream = BytesIO(contenido)
    respuesta = FileResponse(stream, content_type=archivo.content_type, filename=f'soporte.{extension}', as_attachment=descargar)
    respuesta['Cache-Control'] = 'private, no-store'
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['Referrer-Policy'] = 'no-referrer'
    respuesta['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return respuesta


@never_cache
@login_required
@require_GET
def qr_view(request, solicitud_id, receptor_id):
    _propiedad(request, solicitud_id)
    receptor = get_object_or_404(ReceptorBrebEducativo.objects.select_related('qr'), pk=receptor_id)
    vigente = servicios.receptor_vigente()
    if not vigente or vigente.pk != receptor.pk:
        return HttpResponse(
            'La version del receptor ya no esta disponible. Recarga la pagina de pagos antes de transferir.',
            status=409, content_type='text/plain; charset=utf-8',
        )
    return _archivo_response(receptor.qr, request.GET.get('download') == '1')


@never_cache
@login_required
@require_GET
def comprobante_view(request, solicitud_id, reporte_id):
    _propiedad(request, solicitud_id)
    reporte = get_object_or_404(_reportes(), pk=reporte_id, solicitud_id=solicitud_id)
    return _archivo_response(reporte.comprobante, True)


def _contexto(request, **extra):
    from financiacion_educativa.dashboards.operaciones.seguimiento import regreso_seguimiento
    return {'seccion_operativa': 'recaudos', 'capacidades_operativas': capacidades_operativas(request.user),
        'seguimiento_regreso': regreso_seguimiento(request), **extra}


def _datos_conservados(request, clase, **kwargs):
    # Only redisplay form fields; the official action must validate them again.
    form = clase(**kwargs)
    if request.method == 'POST':
        form.initial.update({campo: request.POST[campo] for campo in form.fields if campo in request.POST})
    return form


def _evidencias_del_operador(usuario):
    return ArchivoRecaudoEducativo.objects.filter(
        creado_por=usuario, finalidad='BANK_EVIDENCE',
    ).prefetch_related('escaneos').order_by('-creado_en', 'pk')[:10]


def _cargar_evidencia(request):
    form = EvidenciaBancariaForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            servicios.registrar_evidencia_bancaria(archivo=form.cleaned_data['archivo'], actor=request.user)
        except ValidationError as error:
            form.add_error('archivo', error)
        else:
            messages.success(request, 'Evidencia registrada. Consulta su estado antes de confirmar el ingreso.')
            form = EvidenciaBancariaForm()
    return form


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_REGISTRAR)
@require_http_methods(['GET', 'POST'])
def registrar_pago_recibido_view(request, application_id):
    from financiacion_educativa.dashboards.operaciones.cartera import obtener_obligacion

    _habilitado()
    fotografia = obtener_obligacion(application_id)
    auxiliar = request.POST.get('accion') in ('evidencia', 'actualizar')
    inicial = {'clave': uuid.uuid4(), 'fecha': timezone.localdate()}
    form = (_datos_conservados(request, RegistrarPagoRecibidoForm, actor=request.user, initial=inicial)
        if auxiliar else RegistrarPagoRecibidoForm(request.POST if request.method == 'POST' else None,
            actor=request.user, initial=inicial))
    evidencia_form = EvidenciaBancariaForm()
    if request.method == 'POST' and request.POST.get('accion') == 'evidencia':
        evidencia_form = _cargar_evidencia(request)
    elif request.method == 'POST' and not auxiliar and form.is_valid():
        try:
            ingreso, existente = servicios.registrar_pago_recibido(solicitud_id=application_id, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            _error(form, error)
        else:
            messages.info(request, 'El movimiento ya estaba registrado. Revise el recaudo existente; no se crearon registros nuevos.'
                if existente else 'Ingreso registrado por Operaciones. Pendiente de aplicacion a cuota.')
            return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=ingreso.reporte_id)
    evidencia_form.use_required_attribute = False
    return render(request, 'financiacion_educativa/recaudos/registrar_recibido.html', _contexto(request,
        fotografia=fotografia, form=form, evidencia_form=evidencia_form,
        evidencias=_evidencias_del_operador(request.user)))


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR)
@require_GET
def bandeja_view(request):
    _habilitado()
    parametros = request.GET.copy()
    parametros.setdefault('estado', 'PENDING')
    form = FiltrosRecaudosForm(parametros)
    reportes = _reportes()
    confirmados = ConciliacionRecaudoEducativo.objects.all()
    if form.is_valid():
        filtros = form.cleaned_data
        if filtros.get('institucion'):
            reportes = reportes.filter(solicitud__institucion_id=filtros['institucion'])
            confirmados = confirmados.filter(reporte__solicitud__institucion_id=filtros['institucion'])
        for clave, lookup in (('desde', 'gte'), ('hasta', 'lte')):
            if filtros.get(clave):
                reportes = reportes.filter(**{f'creado_en__date__{lookup}': filtros[clave]})
                confirmados = confirmados.filter(**{f'fecha_efectiva__{lookup}': filtros[clave]})
    elif request.GET:
        reportes, confirmados = reportes.none(), confirmados.none()
    pendientes = reportes.filter(conciliacion__isnull=True, rechazo__isnull=True)
    por_aplicar = reportes.filter(conciliacion__isnull=False, dinero_sin_destino__gt=0)
    aplicados = reportes.filter(dinero_aplicado__gt=0)
    pestanas = []
    for estado, etiqueta, grupo in (
        ('PENDING', 'Por verificar', pendientes), ('TO_APPLY', 'Por aplicar', por_aplicar),
        ('APPLIED', 'Aplicados', aplicados),
    ):
        enlace = parametros.copy()
        enlace.pop('page', None)
        enlace['estado'] = estado
        pestanas.append({'etiqueta': etiqueta, 'cantidad': grupo.count(), 'querystring': enlace.urlencode(),
            'activa': form.is_valid() and form.cleaned_data.get('estado') == estado})
    pendiente = pendientes.aggregate(total=Sum('monto_reportado'))['total'] or Decimal('0')
    if form.is_valid():
        estado = form.cleaned_data.get('estado')
        if estado == 'CONFIRMED':
            reportes = reportes.filter(conciliacion__isnull=False)
        elif estado == 'REJECTED':
            reportes = reportes.filter(rechazo__isnull=False)
        elif estado in ('PENDING', 'TO_APPLY', 'APPLIED'):
            reportes = {'PENDING': pendientes, 'TO_APPLY': por_aplicar, 'APPLIED': aplicados}[estado]
    confirmado = confirmados.aggregate(total=Sum('monto_recibido'))['total'] or Decimal('0')
    parametros.pop('page', None)
    return render(request, 'financiacion_educativa/recaudos/bandeja.html', _contexto(request,
        form=form, pagina=Paginator(reportes, 25).get_page(request.GET.get('page')),
        querystring=parametros.urlencode(), pendiente=pendiente, confirmado=confirmado, pestanas=pestanas,
    ))


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR)
@require_GET
def detalle_operativo_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    return _render_detalle(request, reporte)


def _render_detalle(request, reporte, *, confirmar_form=None, evidencia_form=None, rechazar_form=None, error_aplicacion=None, aplicar_form=None):
    cuota_aplicable = None
    ingreso = getattr(reporte, 'conciliacion', None)
    puede_aplicar = request.user.has_perm(aplicacion_cuotas.PERMISO_APLICAR)
    if puede_aplicar and ingreso and not hasattr(ingreso, 'aplicacion'):
        try:
            cuota_aplicable, vista_previa = aplicacion_cuotas.preparar_vista_previa(reporte=reporte, actor=request.user)
            if aplicar_form is None:
                aplicar_form = AplicarCuotaForm(initial={'cuota': cuota_aplicable.pk, 'vista_previa': vista_previa})
        except ValidationError as error:
            error_aplicacion = error_aplicacion or error.messages[0]
    # Evidencias del operador que no son QR ni comprobantes de pagadores.
    evidencias = _evidencias_del_operador(request.user)
    evidencia_form = evidencia_form if evidencia_form is not None else EvidenciaBancariaForm()
    evidencia_form.use_required_attribute = False
    confirmar_form = confirmar_form if confirmar_form is not None else ConfirmarRecaudoForm(
        actor=request.user, initial={'monto': reporte.monto_reportado, 'fecha': reporte.fecha_declarada})
    try:
        monto_visible = Decimal(str(confirmar_form['monto'].value()))
        mostrar_diferencia = monto_visible.is_finite() and monto_visible != reporte.monto_reportado
    except (InvalidOperation, ValueError):
        mostrar_diferencia = False
    return render(request, 'financiacion_educativa/recaudos/detalle_operativo.html', _contexto(request,
        reporte=reporte, confirmar_form=confirmar_form, mostrar_diferencia=mostrar_diferencia,
        evidencia_form=evidencia_form,
        rechazar_form=rechazar_form if rechazar_form is not None else RechazarReporteForm(),
        puede_conciliar=request.user.is_active and request.user.has_perm(servicios.PERMISO_CONCILIAR),
        ultimo_escaneo=reporte.comprobante.escaneos.order_by('-numero').first() if reporte.comprobante_id else None,
        evidencias=evidencias, cuota_aplicable=cuota_aplicable, error_aplicacion=error_aplicacion,
        aplicar_form=aplicar_form,
    ))


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, aplicacion_cuotas.PERMISO_APLICAR)
@require_POST
def aplicar_cuota_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    form = AplicarCuotaForm(request.POST)
    if form.is_valid():
        try:
            aplicacion_cuotas.aplicar_cuota(
                reporte_id=reporte_id, cuota_id=form.cleaned_data['cuota'],
                vista_previa=form.cleaned_data['vista_previa'],
                confirmar_cuota=form.cleaned_data['confirmar_cuota'], actor=request.user,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Pago aplicado a la cuota.')
            return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte_id)
    return _render_detalle(request, get_object_or_404(_reportes(), pk=reporte_id), aplicar_form=form)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_CONCILIAR)
@require_POST
def confirmar_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    if reporte.origen == 'OPERATIONS':
        return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte.pk)
    form = ConfirmarRecaudoForm(request.POST, actor=request.user)
    if form.is_valid():
        try:
            servicios.confirmar_recaudo(reporte_id=reporte_id, actor=request.user, cuenta_id=reporte.receptor.cuenta_id, **form.cleaned_data)
        except servicios.MovimientoBancarioRegistrado as error:
            messages.info(request, 'El movimiento ya estaba registrado. Consulte el recaudo existente; este reporte no se concilio de nuevo.')
            return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=error.reporte_id)
        except (ValidationError, ArchivoRecaudoEducativo.DoesNotExist) as error:
            _error(form, error if isinstance(error, ValidationError) else ValidationError({'evidencia_id': 'Evidencia no disponible.'}))
        else:
            messages.success(request, 'Ingreso confirmado pendiente de aplicacion. No se modifico el contrato ni el saldo.')
            return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte_id)
    return _render_detalle(request, reporte, confirmar_form=form)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_CONCILIAR)
@require_POST
def rechazar_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    form = RechazarReporteForm(request.POST)
    if form.is_valid():
        try:
            servicios.rechazar_reporte(reporte_id=reporte_id, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            _error(form, error)
        else:
            messages.success(request, 'Reporte no conciliado. El motivo quedo registrado para el pagador.')
            return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte_id)
    return _render_detalle(request, reporte, rechazar_form=form)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_CONCILIAR)
@require_POST
def evidencia_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    form = EvidenciaBancariaForm() if request.POST.get('accion') == 'actualizar' else _cargar_evidencia(request)
    confirmar_form = _datos_conservados(request, ConfirmarRecaudoForm, actor=request.user,
        initial={'monto': reporte.monto_reportado, 'fecha': reporte.fecha_declarada})
    return _render_detalle(request, reporte, evidencia_form=form, confirmar_form=confirmar_form)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR)
@require_GET
def evidencia_propia_view(request, archivo_id):
    _habilitado()
    if not request.user.has_perm(servicios.PERMISO_REGISTRAR) and not request.user.has_perm(servicios.PERMISO_CONCILIAR):
        raise PermissionDenied
    archivo = get_object_or_404(ArchivoRecaudoEducativo, pk=archivo_id,
        creado_por=request.user, finalidad='BANK_EVIDENCE')
    return _archivo_response(archivo)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR)
@require_GET
def soporte_operativo_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    archivo = reporte.comprobante
    if request.GET.get('banco') == '1':
        if not hasattr(reporte, 'conciliacion'):
            raise Http404
        archivo = reporte.conciliacion.evidencia_movimiento
    return _archivo_response(archivo, True)
