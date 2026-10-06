import uuid
from decimal import Decimal

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
from financiacion_educativa.services import recaudos as servicios
from financiacion_educativa.dashboards.operaciones.permissions import requiere_permisos_operativos, capacidades_operativas
from .forms_recaudos import (
    ReportarPagoForm, ConfirmarRecaudoForm, EvidenciaBancariaForm, RechazarReporteForm, FiltrosRecaudosForm,
)


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
    form.add_error(None, '; '.join(error.messages))


def _reportes():
    return ReporteRecaudoEducativo.objects.select_related(
        'solicitud__institucion', 'receptor__cuenta', 'comprobante', 'conciliacion', 'rechazo',
    ).order_by('-creado_en', 'pk')


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def pagos_view(request, solicitud_id):
    fotografia = _propiedad(request, solicitud_id)
    receptor = servicios.receptor_vigente()
    form = ReportarPagoForm(request.POST or None, request.FILES or None, initial={
        'clave': uuid.uuid4(), 'receptor_id': receptor.pk if receptor else None,
        'monto': fotografia.proxima_cuota, 'fecha': timezone.localdate(),
    })
    if request.method == 'POST' and form.is_valid():
        try:
            reporte, replay = servicios.reportar_pago(solicitud_id=solicitud_id, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            _error(form, error)
        else:
            messages.success(request, 'Reporte recibido. La confirmacion requiere verificar el ingreso en el banco.' if not replay else 'Este reporte ya fue recibido; no se creo otro.')
            return redirect('financiacion_educativa_web:reporte-pago', solicitud_id=solicitud_id, reporte_id=reporte.pk)
    return render(request, 'financiacion_educativa/recaudos/reportar.html', {
        'fotografia': fotografia, 'solicitud': fotografia.solicitud, 'receptor': receptor, 'form': form,
        'reportes': _reportes().filter(solicitud_id=solicitud_id),
    })


@never_cache
@login_required
@require_GET
def reporte_view(request, solicitud_id, reporte_id):
    fotografia = _propiedad(request, solicitud_id)
    reporte = get_object_or_404(_reportes(), pk=reporte_id, solicitud_id=solicitud_id)
    return render(request, 'financiacion_educativa/recaudos/estado.html', {'reporte': reporte, 'solicitud': fotografia.solicitud})


def _archivo_response(archivo, descargar=False):
    if archivo.estado_escaneo != 'SAFE':
        raise Http404('El soporte todavia no esta disponible para consulta.')
    extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'application/pdf': 'pdf'}[archivo.content_type]
    try:
        stream = archivo.archivo.open('rb')
    except (OSError, ValueError):
        raise Http404('Soporte no disponible.') from None
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
    return {'seccion_operativa': 'recaudos', 'capacidades_operativas': capacidades_operativas(request.user), **extra}


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR)
@require_GET
def bandeja_view(request):
    _habilitado()
    form = FiltrosRecaudosForm(request.GET or None)
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
        if filtros.get('estado') == 'CONFIRMED':
            reportes = reportes.filter(conciliacion__isnull=False)
        elif filtros.get('estado') == 'REJECTED':
            reportes = reportes.filter(rechazo__isnull=False)
        elif filtros.get('estado') == 'PENDING':
            reportes = reportes.filter(conciliacion__isnull=True, rechazo__isnull=True)
    elif request.GET:
        reportes, confirmados = reportes.none(), confirmados.none()
    pendiente = reportes.filter(conciliacion__isnull=True, rechazo__isnull=True).aggregate(total=Sum('monto_reportado'))['total'] or Decimal('0')
    confirmado = confirmados.aggregate(total=Sum('monto_recibido'))['total'] or Decimal('0')
    parametros = request.GET.copy()
    parametros.pop('page', None)
    return render(request, 'financiacion_educativa/recaudos/bandeja.html', _contexto(request,
        form=form, pagina=Paginator(reportes, 25).get_page(request.GET.get('page')),
        querystring=parametros.urlencode(), pendiente=pendiente, confirmado=confirmado,
    ))


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR)
@require_GET
def detalle_operativo_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    # Evidencias del operador que no son QR ni comprobantes de pagadores.
    evidencias = ArchivoRecaudoEducativo.objects.filter(creado_por=request.user, finalidad='BANK_EVIDENCE').prefetch_related('escaneos').order_by('-creado_en')[:10]
    return render(request, 'financiacion_educativa/recaudos/detalle_operativo.html', _contexto(request,
        reporte=reporte, confirmar_form=ConfirmarRecaudoForm(initial={'monto': reporte.monto_reportado, 'fecha': timezone.localdate()}),
        evidencia_form=EvidenciaBancariaForm(), rechazar_form=RechazarReporteForm(),
        puede_conciliar=request.user.is_active and request.user.has_perm(servicios.PERMISO_CONCILIAR),
        ultimo_escaneo=reporte.comprobante.escaneos.order_by('-numero').first(),
        evidencias=evidencias,
    ))


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_CONCILIAR)
@require_POST
def confirmar_view(request, reporte_id):
    _habilitado()
    reporte = get_object_or_404(_reportes(), pk=reporte_id)
    form = ConfirmarRecaudoForm(request.POST)
    if form.is_valid():
        try:
            servicios.confirmar_recaudo(reporte_id=reporte_id, actor=request.user, cuenta_id=reporte.receptor.cuenta_id, **form.cleaned_data)
        except (ValidationError, ArchivoRecaudoEducativo.DoesNotExist) as error:
            messages.error(request, '; '.join(error.messages) if isinstance(error, ValidationError) else 'Evidencia no disponible.')
        else:
            messages.success(request, 'Ingreso confirmado pendiente de aplicacion. No se modifico el contrato ni el saldo.')
    else:
        messages.error(request, 'Revise los datos de conciliacion: monto, fecha, referencia, evidencia y observacion son obligatorios.')
    return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte_id)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_CONCILIAR)
@require_POST
def rechazar_view(request, reporte_id):
    _habilitado()
    form = RechazarReporteForm(request.POST)
    if form.is_valid():
        try:
            servicios.rechazar_reporte(reporte_id=reporte_id, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
    return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte_id)


@never_cache
@requiere_permisos_operativos(servicios.PERMISO_CONSULTAR, servicios.PERMISO_CONCILIAR)
@require_POST
def evidencia_view(request, reporte_id):
    _habilitado()
    get_object_or_404(_reportes(), pk=reporte_id)
    form = EvidenciaBancariaForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            evidencia = servicios.registrar_evidencia_bancaria(archivo=form.cleaned_data['archivo'], actor=request.user)
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        else:
            messages.success(request, f'Evidencia registrada: {evidencia.pk}. Pendiente de escaneo de seguridad.')
    else:
        messages.error(request, 'Adjunte una evidencia bancaria valida.')
    return redirect('financiacion_educativa_web:operaciones:recaudo-detalle', reporte_id=reporte_id)


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
