from django.contrib.auth import logout
from pathlib import Path
from django.http import Http404
from django.http import JsonResponse
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import set_urlconf
from django.views.decorators.http import require_POST

from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction

from contractors.forms import (
    FormularioDocumentoSolicitudContratista,
    FormularioSimulacionContratista,
    FormularioSolicitudContratista,
)
from contractors.models import (
    ContractorApplicationDocument,
    TAMANO_MAXIMO_DOCUMENTO_BYTES,
)
from contractors.selectors import (
    listar_documentos_solicitud_contratista,
    obtener_solicitud_contratista,
)
from contractors.services.branding import obtener_contexto_branding_con_defaults
from contractors.services.documentos import (
    DatosDocumentoSolicitudContratista,
    registrar_documento_solicitud_contratista,
)
from contractors.services.datos_contractuales import (
    DatosContractualesContratista,
    ErrorDatosContractualesContratista,
    registrar_datos_contractuales_contratista,
)
from contractors.services.analisis_contrato_ia import (
    analizar_contrato_con_openai,
    validar_resultado_analisis_contrato,
)
from contractors.services.simulation import ErrorSimulacionContratista, simular_credito_portal_contratistas
from contractors.services.solicitudes import DatosSolicitudContratista, crear_solicitud_contratista
from gestion_creditos.models import Empresa
from usuariocreditos.views import dashboard_libranza_view
from usuarios.models import ProductAccessProfile
from usuarios.views import ProductLoginView, ProductRegisterView


class VistaLoginContratistas(ProductLoginView):
    template_name = 'account/libranza/login.html'
    next_default_url = '/solicitar/'
    target_flow = ProductAccessProfile.ProductFlow.LIBRANZA
    registration_url_name = 'contractors:registro_contratistas'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'producto_auth_nombre': 'Contratistas Aprobado',
                'badge_auth_texto': 'Acceso contratistas',
                'descripcion_auth_texto': (
                    'Accede con tu correo o continua con Google para registrar tu informacion '
                    'contractual y cargar documentos.'
                ),
                'back_url': '/',
                'volver_auth_texto': 'Volver a contratistas',
                'ocultar_recuperar_password': True,
            },
        )
        return context


class VistaRegistroContratistas(ProductRegisterView):
    template_name = 'account/libranza/register.html'
    next_default_url = '/solicitar/'
    target_flow = ProductAccessProfile.ProductFlow.LIBRANZA
    login_url_name = 'contractors:login_contratistas'
    landing_url_name = 'contractors:landing_contratista'

    def _build_context(self, form):
        context = super()._build_context(form)
        context.update(self._contexto_auth_contratistas())
        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._contexto_auth_contratistas())
        return context

    @staticmethod
    def _contexto_auth_contratistas():
        return {
            'producto_auth_nombre': 'Contratistas Aprobado',
            'badge_auth_texto': 'Nueva cuenta contratistas',
            'descripcion_registro_texto': (
                'Crea tu acceso para registrar informacion contractual, seleccionar empresa '
                'y cargar documentos.'
            ),
            'volver_auth_texto': 'Volver a contratistas',
        }


@require_POST
def logout_contratistas_view(request):
    logout(request)
    return redirect('contractors:login_contratistas')


def landing_contratista_view(request):
    _obtener_configuracion_portal_activa(request)
    return redirect('contractors:solicitud_contratista')


@login_required(login_url='/login/')
def mi_credito_contratista_view(request):
    _obtener_configuracion_portal_activa(request)
    request.urlconf = 'aprobado_web.urls_main'
    set_urlconf('aprobado_web.urls_main')
    try:
        return dashboard_libranza_view(request)
    finally:
        set_urlconf(None)


@login_required(login_url='/login/')
def simulador_contratista_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    solicitud_id = request.GET.get('solicitud_id')
    if not solicitud_id:
        return redirect('contractors:solicitud_contratista')

    solicitud = obtener_solicitud_contratista(
        solicitud_id,
        configuracion_portal=configuracion_portal,
        usuario=request.user,
    )
    if not solicitud:
        raise Http404('solicitud_contratista_no_encontrada')

    branding = obtener_contexto_branding_con_defaults(configuracion_portal)
    resultado = None

    if request.method == 'POST':
        formulario = FormularioSimulacionContratista(request.POST)
        if formulario.is_valid():
            try:
                resultado = simular_credito_portal_contratistas(
                    configuracion_portal=configuracion_portal,
                    monto=formulario.cleaned_data['monto'],
                    plazo_meses=formulario.cleaned_data['plazo_meses'],
                )
            except ErrorSimulacionContratista as exc:
                formulario.add_error(None, str(exc))
    else:
        formulario = FormularioSimulacionContratista(
            initial={
                'monto': solicitud.requested_amount,
                'plazo_meses': solicitud.term_months,
            },
        )

    return render(
        request,
        'contractors/simulador_contratista.html',
        {
            'branding': branding,
            'configuracion_portal': configuracion_portal,
            'configuracion_producto': configuracion_portal,
            'formulario': formulario,
            'organizacion': None,
            'resultado': resultado,
            'solicitud': solicitud,
        },
    )


@login_required(login_url='/login/')
def solicitud_contratista_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)

    branding = obtener_contexto_branding_con_defaults(configuracion_portal)

    if request.method == 'POST':
        formulario = FormularioSolicitudContratista(
            request.POST,
            request.FILES,
            configuracion_producto=configuracion_portal,
        )
        if formulario.is_valid():
            try:
                resultado_analisis_contrato = analizar_contrato_con_openai(
                    formulario.cleaned_data['contrato_actual'],
                )
                validar_resultado_analisis_contrato(resultado_analisis_contrato)
                resultado_simulacion = simular_credito_portal_contratistas(
                    configuracion_portal=configuracion_portal,
                    monto=formulario.cleaned_data['monto'],
                    plazo_meses=formulario.cleaned_data['plazo_meses'],
                )
                payload_simulacion = _payload_simulacion(resultado_simulacion)
                payload_simulacion['analisis_contrato_ia'] = resultado_analisis_contrato.metadata_segura()
                datos = DatosSolicitudContratista(
                    monto_solicitado=formulario.cleaned_data['monto'],
                    plazo_meses=formulario.cleaned_data['plazo_meses'],
                    tipo_documento=formulario.cleaned_data['tipo_documento'],
                    numero_documento=formulario.cleaned_data['numero_documento'],
                    nombres=formulario.cleaned_data['nombres'],
                    apellidos=formulario.cleaned_data['apellidos'],
                    celular=formulario.cleaned_data['celular'],
                    correo=formulario.cleaned_data['correo'],
                    escenario_credito=formulario.cleaned_data['escenario_credito'],
                    direccion=formulario.cleaned_data['direccion'],
                    terminos_aceptados=formulario.cleaned_data['terminos_aceptados'],
                    cuota_mensual_estimada=resultado_simulacion.cuota_mensual,
                    payload_simulacion=payload_simulacion,
                    subdominio_origen=configuracion_portal.slug,
                    ip_address=_obtener_ip_cliente(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                )
                datos_contractuales = DatosContractualesContratista(
                    cargo=formulario.cleaned_data['cargo'],
                    tipo_contrato=formulario.cleaned_data['tipo_contrato'],
                    empresa=formulario.cleaned_data['empresa'],
                    fecha_inicio_contrato=formulario.cleaned_data['fecha_inicio_contrato'],
                    fecha_fin_contrato=formulario.cleaned_data['fecha_fin_contrato'],
                    valor_total_contrato=formulario.cleaned_data['valor_total_contrato'],
                    valor_pagado_contrato=formulario.cleaned_data['valor_pagado_contrato'],
                    valor_pendiente_cobrar=formulario.cleaned_data['valor_pendiente_cobrar'],
                    observaciones=formulario.cleaned_data['observaciones'],
                )
                with transaction.atomic():
                    resultado_solicitud = crear_solicitud_contratista(
                        configuracion_portal=configuracion_portal,
                        datos=datos,
                        usuario=request.user,
                    )
                    registrar_datos_contractuales_contratista(
                        solicitud=resultado_solicitud.solicitud,
                        datos=datos_contractuales,
                    )
                    documentos_registrados = _registrar_documentos_iniciales(
                        solicitud=resultado_solicitud.solicitud,
                        formulario=formulario,
                    )
                    contrato_registrado = documentos_registrados.get(
                        ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
                    )
                    if contrato_registrado:
                        resultado_solicitud.solicitud.simulation_payload['analisis_contrato_ia'] = (
                            resultado_analisis_contrato.metadata_segura(
                                documento_id=contrato_registrado.documento_id,
                            )
                        )
                        resultado_solicitud.solicitud.save(update_fields=['simulation_payload', 'updated_at'])
                return redirect(f'/simular/?solicitud_id={resultado_solicitud.solicitud_id}')
            except (ErrorSimulacionContratista, ErrorDatosContractualesContratista, ValidationError) as exc:
                formulario.add_error(None, _mensajes_validacion(exc))
    else:
        formulario = FormularioSolicitudContratista(configuracion_producto=configuracion_portal)

    return render(
        request,
        'contractors/solicitud_contratista.html',
        {
            'branding': branding,
            'configuracion_portal': configuracion_portal,
            'configuracion_producto': configuracion_portal,
            'formulario': formulario,
            'organizacion': None,
        },
    )


@login_required(login_url='/login/')
def documentos_solicitud_contratista_view(request, solicitud_id):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    solicitud = obtener_solicitud_contratista(
        solicitud_id,
        configuracion_portal=configuracion_portal,
        usuario=request.user,
    )
    if not solicitud:
        raise Http404('solicitud_contratista_no_encontrada')

    branding = obtener_contexto_branding_con_defaults(configuracion_portal)
    documento_registrado = None

    if request.method == 'POST':
        formulario = FormularioDocumentoSolicitudContratista(request.POST, request.FILES, solicitud=solicitud)
        if formulario.is_valid():
            archivo = formulario.cleaned_data['archivo']
            datos = DatosDocumentoSolicitudContratista(
                tipo_documento=formulario.cleaned_data['tipo_documento'],
                archivo=archivo,
                nombre_original=archivo.name,
                content_type=getattr(archivo, 'content_type', ''),
                tamano_archivo=getattr(archivo, 'size', 0),
            )
            try:
                documento_registrado = registrar_documento_solicitud_contratista(
                    solicitud=solicitud,
                    datos=datos,
                )
                formulario = FormularioDocumentoSolicitudContratista(solicitud=solicitud)
            except ValidationError as exc:
                formulario.add_error(None, _mensajes_validacion(exc))
    else:
        formulario = FormularioDocumentoSolicitudContratista(solicitud=solicitud)

    documentos = listar_documentos_solicitud_contratista(solicitud)
    return render(
        request,
        'contractors/documentos_solicitud_contratista.html',
        {
            'branding': branding,
            'documento_registrado': documento_registrado,
            'documentos': documentos,
            'formulario': formulario,
            'configuracion_portal': configuracion_portal,
            'organizacion': None,
            'solicitud': solicitud,
        },
    )


def buscar_empresas_contratistas_view(request):
    _obtener_configuracion_portal_activa(request)
    query = (request.GET.get('q') or '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    empresas = (
        Empresa.objects
        .filter(convenio_activo=True)
        .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
        .filter(nombre__icontains=query)
        .order_by('nombre')[:8]
    )
    empresas_razon_social = (
        Empresa.objects
        .filter(convenio_activo=True)
        .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
        .filter(razon_social__icontains=query)
        .exclude(pk__in=[empresa.pk for empresa in empresas])
        .order_by('nombre')[:8]
    )
    resultados = list(empresas) + list(empresas_razon_social)
    return JsonResponse({
        'results': [
            {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'razon_social': empresa.razon_social,
                'nit': empresa.nit,
            }
            for empresa in resultados[:8]
        ]
    })


@login_required(login_url='/login/')
@require_POST
def analizar_contrato_contratista_view(request):
    _obtener_configuracion_portal_activa(request)
    contrato = request.FILES.get('contrato_actual') or request.FILES.get('contrato')
    if not contrato:
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': 'Carga el contrato vigente en PDF.',
            },
            status=400,
        )

    error_pdf = _validar_pdf_temporal_contrato(contrato)
    if error_pdf:
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': error_pdf,
            },
            status=400,
        )

    if not _tratamiento_datos_aceptado(request):
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': False,
                'error': 'Debes aceptar la autorizacion de tratamiento de datos antes de analizar el contrato.',
            },
            status=400,
        )

    if not _permitir_analisis_contrato(request):
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': 'Has realizado varios analisis en poco tiempo. Intenta nuevamente en unos segundos.',
            },
            status=429,
        )

    resultado = analizar_contrato_con_openai(contrato)
    if resultado.habilitado and resultado.exito and resultado.es_contrato is False:
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': False,
                'es_contrato': False,
                'error': 'El documento cargado no parece ser un contrato valido.',
            },
        )

    if not resultado.habilitado or not resultado.exito:
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'es_contrato': resultado.es_contrato,
                'error': _mensaje_error_analisis_contrato(resultado),
                'metadata': resultado.metadata_segura(),
            },
        )

    return JsonResponse(
        {
            'success': True,
            'manual_allowed': True,
            'es_contrato': resultado.es_contrato,
            'datos': resultado.datos_autocompletado(),
            'campos_no_encontrados': list(resultado.campos_no_encontrados),
            'advertencias': list(resultado.advertencias),
            'confianza_general': float(resultado.confianza_general),
            'requiere_confirmacion_usuario': resultado.requiere_confirmacion_usuario,
            'metadata': resultado.metadata_segura(),
        },
    )


def terminos_condiciones_contratistas_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    return render(
        request,
        'contractors/legal_contratistas.html',
        {
            'branding': obtener_contexto_branding_con_defaults(configuracion_portal),
            'tipo_legal': 'terminos',
            'titulo': 'Términos y Condiciones',
            'fecha_actualizacion': 'Mayo de 2026',
            'contenido': (
                'Estos términos regulan el uso del portal digital de contratistas de Aprobado. '
                'Aplican junto con las condiciones generales de los canales digitales de Aprobado y con las '
                'autorizaciones que el usuario acepte durante el registro, cargue documental y validación de su solicitud.'
            ),
            'nota_contratistas': (
                'El portal permite iniciar una solicitud como contratista con contrato vigente; la radicación, '
                'carga de documentos o simulación no constituye aprobación automática ni obligación de desembolso.'
            ),
            'secciones': [
                (
                    'Objeto del portal',
                    'Aprobado habilita el portal de contratistas para que personas con contrato vigente registren su información, seleccionen una empresa existente del ecosistema Aprobado, carguen documentos y avancen en una evaluación preliminar de crédito de libranza o adelanto asociado a su contrato.',
                ),
                (
                    'Condiciones de uso',
                    'El usuario debe utilizar el portal únicamente para gestionar su propia solicitud, mantener la confidencialidad de sus credenciales y abstenerse de cargar documentos alterados, incompletos, ilegibles o que no correspondan a su identidad y relación contractual.',
                ),
                (
                    'Registro de usuario',
                    'Para acceder al flujo de solicitud, el usuario debe autenticarse o crear una cuenta. Aprobado podrá usar los datos registrados para identificar al solicitante, dar continuidad al proceso y conservar trazabilidad operativa del estado de la solicitud.',
                ),
                (
                    'Veracidad de la información',
                    'La información personal, contractual, laboral, financiera y documental suministrada debe ser veraz, completa y actualizada. La entrega de información falsa o inexacta puede generar rechazo de la solicitud, bloqueo del proceso o acciones permitidas por la ley.',
                ),
                (
                    'Carga documental',
                    'El solicitante debe cargar los documentos requeridos, incluyendo documento de identidad, contrato vigente y certificado bancario cuando aplique. Estos documentos se usan para revisión de identidad, relación contractual, empresa contratante, valores, vigencia y consistencia de la solicitud.',
                ),
                (
                    'Validaciones internas',
                    'Aprobado podrá realizar revisión documental, validación de capacidad contractual, evaluación de riesgo, verificación de crédito previo, reglas de segundo crédito o recogida de cartera, y otras validaciones internas o externas que se habiliten en fases posteriores.',
                ),
                (
                    'Simulación y no aprobación automática',
                    'Los valores simulados son informativos. La simulación no garantiza aprobación, cupo, tasa, plazo, comisión, desembolso ni emisión de pagaré. Las condiciones definitivas dependen de la revisión documental, políticas vigentes, capacidad, riesgo y aprobaciones internas.',
                ),
                (
                    'Empresa y pagador existente',
                    'El solicitante debe seleccionar una empresa existente en Aprobado. El portal público no crea empresas ni pagadores. Cuando el flujo avance a etapas productivas, el pagador podrá recibir la novedad operativa que corresponda según las reglas del producto.',
                ),
                (
                    'Responsabilidad del usuario',
                    'El usuario es responsable de revisar la información registrada, confirmar los datos extraídos o solicitados, corregir inconsistencias oportunamente y leer cuidadosamente cualquier autorización, contrato, pagaré o documento legal antes de aceptarlo o firmarlo.',
                ),
                (
                    'Protección de datos',
                    'El tratamiento de datos personales se rige por la política de privacidad de Aprobado, la autorización otorgada por el titular y las normas colombianas aplicables. El usuario puede ejercer sus derechos a través de los canales de atención publicados.',
                ),
                (
                    'Modificaciones',
                    'Aprobado podrá actualizar estos términos para reflejar cambios legales, operativos, tecnológicos o de producto. La versión vigente estará disponible en el portal y tendrá efecto desde su publicación, salvo disposición legal diferente.',
                ),
            ],
        },
    )


def politica_privacidad_contratistas_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    return render(
        request,
        'contractors/legal_contratistas.html',
        {
            'branding': obtener_contexto_branding_con_defaults(configuracion_portal),
            'tipo_legal': 'privacidad',
            'titulo': 'Política de Privacidad',
            'fecha_actualizacion': 'Mayo de 2026',
            'contenido': (
                'Esta política describe el tratamiento de datos personales realizado por Aprobado en el portal '
                'de contratistas, de conformidad con la Ley 1581 de 2012, el Decreto 1377 de 2013 y las normas '
                'colombianas aplicables en materia de protección de datos personales.'
            ),
            'nota_contratistas': (
                'La información suministrada en el flujo de contratistas se usa para registrar la solicitud, '
                'validar identidad, revisar documentos, evaluar capacidad contractual y atender el proceso de crédito.'
            ),
            'secciones': [
                (
                    'Responsable del tratamiento',
                    'APROBADO SOLUCIONES DIGITALES SAS actúa como responsable del tratamiento de los datos personales recolectados a través del portal de contratistas, formularios, documentos cargados y canales digitales asociados. Los canales de contacto son Info@aprobado.com.co y +57 315 856 2162.',
                ),
                (
                    'Datos recolectados',
                    'Podemos recolectar datos de identificación, contacto, dirección, correo electrónico, celular, información contractual, empresa seleccionada, tipo de contrato, fechas, valores, documentos de identidad, contrato vigente, certificado bancario, IP, navegador y trazabilidad del uso del portal.',
                ),
                (
                    'Finalidad del tratamiento',
                    'Los datos se tratan para registrar solicitudes, validar identidad, revisar documentos, estimar condiciones, evaluar capacidad contractual, aplicar políticas de riesgo, prevenir fraude, atender consultas, conservar trazabilidad, cumplir obligaciones legales y mejorar la operación del portal.',
                ),
                (
                    'Tratamiento de documentos',
                    'Los documentos cargados, incluyendo cédula, contrato vigente y certificado bancario, se usan para el análisis de la solicitud, validaciones internas, verificación de identidad, revisión contractual y cumplimiento de obligaciones legales u operativas. No se publican ni se entregan a terceros no autorizados.',
                ),
                (
                    'Autorización y consentimiento',
                    'Al aceptar esta política y continuar con el registro, el titular autoriza el tratamiento de sus datos para las finalidades descritas. Cuando una finalidad requiera autorización adicional o expresa, Aprobado podrá solicitarla mediante los mecanismos digitales disponibles.',
                ),
                (
                    'Derechos del titular',
                    'El titular puede conocer, actualizar, rectificar y solicitar supresión de sus datos cuando sea procedente; solicitar prueba de la autorización; ser informado sobre el uso de sus datos; presentar quejas ante la Superintendencia de Industria y Comercio; y revocar la autorización en los casos permitidos por la ley.',
                ),
                (
                    'Canales de atención',
                    'Para consultas, reclamos, actualización de datos, solicitudes de supresión o ejercicio de derechos, el titular puede escribir a Info@aprobado.com.co o comunicarse al +57 315 856 2162. Aprobado atenderá las solicitudes conforme a los términos legales aplicables.',
                ),
                (
                    'Seguridad de la información',
                    'Aprobado aplica medidas administrativas, técnicas y organizacionales razonables para proteger la información contra acceso no autorizado, pérdida, alteración, uso indebido o divulgación no autorizada. El acceso interno se limita según roles y necesidades operativas.',
                ),
                (
                    'Encargados y terceros',
                    'Aprobado podrá apoyarse en proveedores tecnológicos, almacenamiento, validación documental, firma electrónica, mensajería, analítica, entidades financieras o autoridades cuando sea requerido, siempre bajo medidas razonables de confidencialidad y seguridad.',
                ),
                (
                    'Vigencia y conservación',
                    'Los datos se conservarán durante el tiempo necesario para cumplir las finalidades autorizadas, atender obligaciones legales, contables, contractuales, tributarias, de auditoría o defensa jurídica, y luego serán tratados conforme a las políticas de conservación y eliminación aplicables.',
                ),
                (
                    'Contacto',
                    'El canal principal de atención para privacidad y solicitudes relacionadas con datos personales es Info@aprobado.com.co. También puedes comunicarte por WhatsApp o teléfono al +57 315 856 2162.',
                ),
            ],
        },
    )


def _obtener_configuracion_portal_activa(request):
    configuracion_portal = getattr(request, 'configuracion_portal_contratistas', None)
    if not configuracion_portal or not configuracion_portal.activo:
        raise Http404('configuracion_portal_contratistas_no_encontrada')
    return configuracion_portal


def _obtener_organizacion_activa(request):
    organizacion = getattr(request, 'contractor_organization', None)
    if not organizacion or not organizacion.is_active:
        raise Http404('organizacion_contratista_no_encontrada')
    return organizacion


def _obtener_ip_cliente(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _validar_pdf_temporal_contrato(archivo):
    content_type = getattr(archivo, 'content_type', '')
    extension = Path(getattr(archivo, 'name', '') or '').suffix.lower()
    tamano = getattr(archivo, 'size', 0) or 0

    if content_type != 'application/pdf' or extension != '.pdf':
        return 'El contrato vigente debe cargarse en PDF.'
    if tamano <= 0:
        return 'El contrato vigente esta vacio.'
    if tamano > TAMANO_MAXIMO_DOCUMENTO_BYTES:
        return 'El contrato vigente supera el tamano maximo permitido.'
    return ''


def _tratamiento_datos_aceptado(request):
    valor = (
        request.POST.get('tratamiento_datos_analisis_ia')
        or request.POST.get('tratamiento_datos_aceptado')
        or ''
    )
    return str(valor).strip().lower() in {'1', 'true', 'on', 'si', 'sí'}


def _permitir_analisis_contrato(request, *, limite=3, ventana=20):
    usuario_id = getattr(request.user, 'id', None) or 'anon'
    ip = _obtener_ip_cliente(request) or 'sin-ip'
    llave = f'contractors:analisis-contrato:{usuario_id}:{ip}'
    try:
        intentos = cache.get(llave, 0)
        if intentos >= limite:
            return False
        cache.set(llave, intentos + 1, ventana)
    except Exception:
        return True
    return True


def _mensaje_error_analisis_contrato(resultado):
    if resultado.error == 'cuota_openai_excedida':
        return (
            'El servicio de IA no esta disponible por cuota o facturacion de OpenAI. '
            'Puedes completar la informacion manualmente.'
        )
    if resultado.error == 'openai_api_key_no_configurada':
        return 'El servicio de IA no esta configurado. Puedes completar la informacion manualmente.'
    if resultado.error == 'ia_deshabilitada':
        return 'El analisis automatico esta deshabilitado. Puedes completar la informacion manualmente.'
    return 'No fue posible analizar automaticamente el contrato. Puedes completar la informacion manualmente.'


def _payload_simulacion(resultado):
    return {llave: str(valor) for llave, valor in resultado.como_dict().items()}


def _registrar_documentos_iniciales(*, solicitud, formulario):
    mapa_documentos = {
        'documento_identidad_frontal': ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL,
        'documento_identidad_reverso': ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO,
        'contrato_actual': ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
        'certificado_bancario': ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    }
    documentos_registrados = {}
    for nombre_campo, tipo_documento in mapa_documentos.items():
        archivo = formulario.cleaned_data[nombre_campo]
        documentos_registrados[tipo_documento] = registrar_documento_solicitud_contratista(
            solicitud=solicitud,
            datos=DatosDocumentoSolicitudContratista(
                tipo_documento=tipo_documento,
                archivo=archivo,
                nombre_original=archivo.name,
                content_type=getattr(archivo, 'content_type', ''),
                tamano_archivo=getattr(archivo, 'size', 0),
            ),
        )
    return documentos_registrados


def _mensajes_validacion(exc):
    if hasattr(exc, 'message_dict'):
        mensajes = []
        for errores in exc.message_dict.values():
            mensajes.extend(str(error) for error in errores)
        return ' '.join(mensajes)
    return str(exc)
