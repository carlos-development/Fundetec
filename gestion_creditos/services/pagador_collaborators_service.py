from decimal import Decimal

from gestion_creditos.models import Credito, CreditoAdelantoNomina, CreditoLibranza, VinculoLaboralEmpresa
from gestion_creditos.services.adelanto_nomina_service import evaluar_elegibilidad_adelanto


COLLABORATOR_STATUS_COMPLETO = 'completo'
COLLABORATOR_STATUS_PENDIENTE_INFO = 'pendiente_info'
COLLABORATOR_STATUS_PENDIENTE_VALIDACION = 'pendiente_validacion'
COLLABORATOR_STATUS_CON_CREDITO_ACTIVO = 'con_credito_activo'
COLLABORATOR_STATUS_PENDIENTE_VINCULO = 'pendiente_vinculo'

COLLABORATOR_STATUS_CHOICES = (
    (COLLABORATOR_STATUS_COMPLETO, 'Completo'),
    (COLLABORATOR_STATUS_PENDIENTE_INFO, 'Pendiente información'),
    (COLLABORATOR_STATUS_PENDIENTE_VALIDACION, 'Pendiente validación'),
    (COLLABORATOR_STATUS_CON_CREDITO_ACTIVO, 'Con crédito activo'),
    (COLLABORATOR_STATUS_PENDIENTE_VINCULO, 'Pendiente validación de convenio'),
)

ACTIVE_CREDIT_STATES = {
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
}


def _normalize_document(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _normalize_text(value):
    return str(value or '').strip()


def _employee_missing_fields(vinculo):
    missing = []
    checks = (
        ('Nombre', vinculo.nombre_empleado),
        ('Documento', vinculo.documento_empleado),
        ('Correo', vinculo.correo_empleado or getattr(vinculo.usuario, 'email', '')),
        ('Fecha alta', vinculo.fecha_alta_aprobado),
        ('Base operativa', vinculo.salario_base_mensual),
    )
    for label, value in checks:
        if value in (None, '', Decimal('0.00')):
            missing.append(label)
    return missing


def _documentation_pending(detalle):
    if not detalle:
        return []
    pending = []
    checks = (
        ('Cédula frontal', getattr(detalle, 'cedula_frontal', None)),
        ('Cédula reverso', getattr(detalle, 'cedula_trasera', None)),
        ('Soporte del convenio', getattr(detalle, 'certificado_laboral', None)),
        ('Certificado bancario', getattr(detalle, 'certificado_bancario', None)),
    )
    for label, file_field in checks:
        if not file_field or not getattr(file_field, 'name', ''):
            pending.append(label)
    return pending


def _new_row(key, *, user=None, documento='', nombre='', correo=''):
    return {
        'key': key,
        'usuario': user,
        'vinculo': None,
        'nombre': _normalize_text(nombre),
        'documento': _normalize_document(documento),
        'correo': _normalize_text(correo).lower(),
        'origenes': set(),
        'creditos_libranza': [],
        'creditos_adelanto': [],
        'credito_principal': None,
        'estado_solicitud': '-',
        'tiene_credito_activo': False,
        'estado_credito': '-',
        'capacidad_pendiente': True,
        'documentacion_pendiente': [],
        'elegibilidad_adelanto': 'Pendiente completar datos del convenio',
        'adelanto_apto': False,
        'monto_maximo': Decimal('0.00'),
        'missing_fields': ['Convenio educativo'],
        'status': COLLABORATOR_STATUS_PENDIENTE_VINCULO,
        'status_label': dict(COLLABORATOR_STATUS_CHOICES)[COLLABORATOR_STATUS_PENDIENTE_VINCULO],
        'acciones': {
            'editar_vinculo': False,
            'ver_credito': False,
            'ver_documentacion': False,
        },
    }


def _row_key(*, user=None, documento=''):
    normalized_document = _normalize_document(documento)
    if normalized_document:
        return f'doc:{normalized_document}'
    if user and getattr(user, 'id', None):
        return f'user:{user.id}'
    return ''


def _get_or_create_row(rows, *, user=None, documento='', nombre='', correo=''):
    key = _row_key(user=user, documento=documento)
    if user and getattr(user, 'id', None):
        for existing in rows.values():
            existing_user = existing.get('usuario')
            if existing_user and existing_user.id == user.id:
                row = existing
                break
        else:
            row = None
        if row:
            if nombre and not row['nombre']:
                row['nombre'] = _normalize_text(nombre)
            if documento and not row['documento']:
                row['documento'] = _normalize_document(documento)
            if correo and not row['correo']:
                row['correo'] = _normalize_text(correo).lower()
            return row
    if not key:
        key = f'tmp:{len(rows) + 1}'
    if key not in rows:
        rows[key] = _new_row(key, user=user, documento=documento, nombre=nombre, correo=correo)
    row = rows[key]
    if user and not row['usuario']:
        row['usuario'] = user
    if nombre and not row['nombre']:
        row['nombre'] = _normalize_text(nombre)
    if documento and not row['documento']:
        row['documento'] = _normalize_document(documento)
    if correo and not row['correo']:
        row['correo'] = _normalize_text(correo).lower()
    return row


def _credit_sort_key(credito):
    return credito.fecha_solicitud or credito.id


def _apply_vinculo(row, vinculo):
    row['vinculo'] = vinculo
    row['usuario'] = vinculo.usuario
    row['nombre'] = vinculo.nombre_empleado or row['nombre']
    row['documento'] = _normalize_document(vinculo.documento_empleado) or row['documento']
    row['correo'] = (vinculo.correo_empleado or getattr(vinculo.usuario, 'email', '') or row['correo']).lower()
    row['origenes'].add('convenio_educativo')
    row['acciones']['editar_vinculo'] = True


def _apply_libranza(row, detalle):
    credito = detalle.credito
    row['origenes'].add('solicitud_credito_estudiantil')
    if credito.estado in ACTIVE_CREDIT_STATES:
        row['origenes'].add('credito_activo')
    row['creditos_libranza'].append(credito)
    row['usuario'] = row['usuario'] or credito.usuario
    row['nombre'] = row['nombre'] or detalle.nombre_completo
    row['documento'] = row['documento'] or _normalize_document(detalle.cedula)
    row['correo'] = row['correo'] or (detalle.correo_electronico or getattr(credito.usuario, 'email', '') or '').lower()
    row['acciones']['ver_credito'] = True
    row['acciones']['ver_documentacion'] = True


def _apply_adelanto(row, detalle):
    credito = detalle.credito
    row['origenes'].add('apoyo_educativo')
    if credito.estado in ACTIVE_CREDIT_STATES:
        row['origenes'].add('credito_activo')
    row['creditos_adelanto'].append(credito)
    row['acciones']['ver_credito'] = True


def _finalize_row(row):
    creditos = sorted(row['creditos_libranza'] + row['creditos_adelanto'], key=_credit_sort_key, reverse=True)
    active_credit = next((credito for credito in creditos if credito.estado in ACTIVE_CREDIT_STATES), None)
    row['credito_principal'] = active_credit or (creditos[0] if creditos else None)
    row['tiene_credito_activo'] = bool(active_credit)
    if row['credito_principal']:
        row['estado_solicitud'] = row['credito_principal'].get_estado_display()
        row['estado_credito'] = active_credit.get_estado_display() if active_credit else row['credito_principal'].get_estado_display()

    pending_docs = []
    for credito in row['creditos_libranza']:
        pending_docs.extend(_documentation_pending(getattr(credito, 'detalle_libranza', None)))
    seen_docs = set()
    row['documentacion_pendiente'] = [
        item for item in pending_docs
        if not (item in seen_docs or seen_docs.add(item))
    ]

    vinculo = row['vinculo']
    if not vinculo:
        row['missing_fields'] = ['Convenio educativo']
        row['capacidad_pendiente'] = True
        row['elegibilidad_adelanto'] = 'Pendiente completar datos del convenio'
        row['status'] = COLLABORATOR_STATUS_PENDIENTE_VINCULO
    else:
        missing = _employee_missing_fields(vinculo)
        row['missing_fields'] = missing
        row['capacidad_pendiente'] = bool(missing)
        eligibility = evaluar_elegibilidad_adelanto(vinculo.usuario)
        row['adelanto_apto'] = bool(
            eligibility.get('eligible')
            and eligibility.get('vinculo')
            and eligibility['vinculo'].id == vinculo.id
        )
        row['monto_maximo'] = eligibility.get('monto_maximo') or Decimal('0.00')
        row['elegibilidad_adelanto'] = (
            'Apto para apoyo educativo'
            if row['adelanto_apto']
            else (eligibility.get('reason') or 'No elegible para apoyo educativo')
        )
        if missing:
            row['status'] = COLLABORATOR_STATUS_PENDIENTE_INFO
        elif not vinculo.validado_por_pagador:
            row['status'] = COLLABORATOR_STATUS_PENDIENTE_VALIDACION
        else:
            row['status'] = COLLABORATOR_STATUS_COMPLETO

    if row['tiene_credito_activo'] and row['status'] == COLLABORATOR_STATUS_COMPLETO:
        row['status'] = COLLABORATOR_STATUS_CON_CREDITO_ACTIVO

    row['status_label'] = dict(COLLABORATOR_STATUS_CHOICES).get(row['status'], row['status'])
    row['origenes'] = sorted(row['origenes'])
    return row


def _matches_search(row, search):
    if not search:
        return True
    haystack = ' '.join([
        row.get('nombre', ''),
        row.get('documento', ''),
        row.get('correo', ''),
    ]).lower()
    return search.lower() in haystack


def _matches_status(row, status_filter):
    if not status_filter:
        return True
    if status_filter == COLLABORATOR_STATUS_CON_CREDITO_ACTIVO:
        return row['tiene_credito_activo']
    return row['status'] == status_filter


def build_pagador_collaborators_context(empresa, search='', estado_filter=''):
    rows = {}

    vinculos = (
        VinculoLaboralEmpresa.objects
        .select_related('usuario', 'empresa')
        .filter(empresa=empresa)
        .order_by('nombre_empleado', 'documento_empleado')
    )
    for vinculo in vinculos:
        row = _get_or_create_row(
            rows,
            user=vinculo.usuario,
            documento=vinculo.documento_empleado,
            nombre=vinculo.nombre_empleado,
            correo=vinculo.correo_empleado,
        )
        _apply_vinculo(row, vinculo)

    detalles_libranza = (
        CreditoLibranza.objects
        .select_related('credito', 'credito__usuario', 'empresa')
        .filter(empresa=empresa)
        .order_by('-credito__fecha_solicitud', '-credito__id')
    )
    for detalle in detalles_libranza:
        row = _get_or_create_row(
            rows,
            user=detalle.credito.usuario,
            documento=detalle.cedula,
            nombre=detalle.nombre_completo,
            correo=detalle.correo_electronico,
        )
        _apply_libranza(row, detalle)

    detalles_adelanto = (
        CreditoAdelantoNomina.objects
        .select_related('credito', 'credito__usuario', 'vinculo_laboral', 'vinculo_laboral__usuario', 'vinculo_laboral__empresa')
        .filter(vinculo_laboral__empresa=empresa)
        .order_by('-credito__fecha_solicitud', '-credito__id')
    )
    for detalle in detalles_adelanto:
        vinculo = detalle.vinculo_laboral
        row = _get_or_create_row(
            rows,
            user=vinculo.usuario,
            documento=vinculo.documento_empleado,
            nombre=vinculo.nombre_empleado,
            correo=vinculo.correo_empleado,
        )
        if not row['vinculo']:
            _apply_vinculo(row, vinculo)
        _apply_adelanto(row, detalle)

    colaboradores = [_finalize_row(row) for row in rows.values()]
    colaboradores = [
        row for row in colaboradores
        if _matches_search(row, search) and _matches_status(row, estado_filter)
    ]
    colaboradores.sort(key=lambda row: (row['nombre'] or row['documento'] or '').upper())

    summary = {
        'total': len(colaboradores),
        'completos': sum(1 for row in colaboradores if row['status'] in {COLLABORATOR_STATUS_COMPLETO, COLLABORATOR_STATUS_CON_CREDITO_ACTIVO}),
        'pendientes_info': sum(1 for row in colaboradores if row['status'] == COLLABORATOR_STATUS_PENDIENTE_INFO),
        'pendientes_validacion': sum(1 for row in colaboradores if row['status'] == COLLABORATOR_STATUS_PENDIENTE_VALIDACION),
        'pendientes_vinculo': sum(1 for row in colaboradores if row['status'] == COLLABORATOR_STATUS_PENDIENTE_VINCULO),
        'con_credito_activo': sum(1 for row in colaboradores if row['tiene_credito_activo']),
        'aptos_adelanto': sum(1 for row in colaboradores if row['adelanto_apto']),
    }

    return {
        'empleados_gestion': colaboradores,
        'empleados_summary': summary,
        'empleado_search': search,
        'empleado_estado': estado_filter,
        'empleado_status_choices': COLLABORATOR_STATUS_CHOICES,
        'vinculo_estado_choices': VinculoLaboralEmpresa.EstadoVinculo.choices,
    }
