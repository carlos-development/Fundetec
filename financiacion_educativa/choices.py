from django.db import models


class EstadoSolicitudFinanciacion(models.TextChoices):
    PENDING_USER_REGISTRATION = 'PENDING_USER_REGISTRATION', 'Pendiente de registro del usuario'
    PENDING_TERMS = 'PENDING_TERMS', 'Pendiente de terminos'
    PENDING_DOCUMENT = 'PENDING_DOCUMENT', 'Pendiente de documento'
    PENDING_GUARDIAN = 'PENDING_GUARDIAN', 'Pendiente de tutor'
    PENDING_MANUAL_REVIEW = 'PENDING_MANUAL_REVIEW', 'Pendiente de revision manual'
    CORRECTION_REQUIRED = 'CORRECTION_REQUIRED', 'Correccion requerida'
    APPROVED = 'APPROVED', 'Aprobada; curso autorizado'
    REJECTED = 'REJECTED', 'Rechazada'
    PENDING_PROMISSORY_NOTE = 'PENDING_PROMISSORY_NOTE', 'Pendiente de pagare'
    PENDING_SIGNATURE = 'PENDING_SIGNATURE', 'Pendiente de firma'
    ACTIVE = 'ACTIVE', 'Activa'
    PAYMENT_REPORTED = 'PAYMENT_REPORTED', 'Pago reportado'
    PAYMENT_UNDER_REVIEW = 'PAYMENT_UNDER_REVIEW', 'Pago en revision'
    PAID = 'PAID', 'Pagada'
    CANCELLED = 'CANCELLED', 'Cancelada'


class RolParticipante(models.TextChoices):
    STUDENT = 'STUDENT', 'Estudiante'
    GUARDIAN = 'GUARDIAN', 'Tutor'
    PRINCIPAL_DEBTOR = 'PRINCIPAL_DEBTOR', 'Deudor principal'


class RelacionEstudiante(models.TextChoices):
    SELF = 'SELF', 'La misma persona'
    MOTHER = 'MOTHER', 'Madre'
    FATHER = 'FATHER', 'Padre'
    LEGAL_GUARDIAN = 'LEGAL_GUARDIAN', 'Representante legal declarado'
    FAMILY_MEMBER = 'FAMILY_MEMBER', 'Familiar'
    OTHER = 'OTHER', 'Otra relacion declarada'


class TipoDocumentoIdentidad(models.TextChoices):
    CC = 'CC', 'Cedula de ciudadania'
    TI = 'TI', 'Tarjeta de identidad'
    CE = 'CE', 'Cedula de extranjeria'
    RC = 'RC', 'Registro civil'
    PASSPORT = 'PASSPORT', 'Pasaporte'
    OTHER = 'OTHER', 'Otro'


class TipoConsentimiento(models.TextChoices):
    TERMS = 'TERMS', 'Terminos y condiciones'
    DATA_PROCESSING = 'DATA_PROCESSING', 'Tratamiento de datos'
    CREDIT_AUTHORIZATION = 'CREDIT_AUTHORIZATION', 'Autorizacion de financiacion'


class TipoDocumentoFinanciacion(models.TextChoices):
    STUDENT_IDENTIFICATION = 'STUDENT_IDENTIFICATION', 'Identificacion del estudiante'
    GUARDIAN_IDENTIFICATION = 'GUARDIAN_IDENTIFICATION', 'Identificacion del tutor'
    DEBTOR_IDENTIFICATION = 'DEBTOR_IDENTIFICATION', 'Identificacion del posible deudor'
    INCOME_CERTIFICATE = 'INCOME_CERTIFICATE', 'Certificado de ingresos'
    ENROLLMENT_EVIDENCE = 'ENROLLMENT_EVIDENCE', 'Evidencia de matricula'
    OTHER_EDUCATIONAL = 'OTHER_EDUCATIONAL', 'Otro documento educativo'
    STUDENT_ID_FRONT = 'STUDENT_ID_FRONT', 'Identificacion del estudiante - frente'
    STUDENT_ID_BACK = 'STUDENT_ID_BACK', 'Identificacion del estudiante - reverso'
    GUARDIAN_ID_FRONT = 'GUARDIAN_ID_FRONT', 'Identificacion del tutor - frente'
    GUARDIAN_ID_BACK = 'GUARDIAN_ID_BACK', 'Identificacion del tutor - reverso'
    OTHER = 'OTHER', 'Otro documento (anterior)'


TIPOS_DOCUMENTO_IDENTIDAD_CAMARA = (
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
)


class EstadoValidacionDocumento(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente de revision'
    APPROVED = 'APPROVED', 'Aceptado'
    REJECTED = 'REJECTED', 'Rechazado'


class EstadoEscaneoDocumento(models.TextChoices):
    PENDING_SECURITY_SCAN = 'PENDING_SECURITY_SCAN', 'Pendiente de escaneo de seguridad'
    SAFE = 'SAFE', 'Sin hallazgos reportados por el escaner'
    BLOCKED = 'BLOCKED', 'Bloqueado por seguridad'


class EstadoIntentoEscaneoDocumento(models.TextChoices):
    STARTED = 'STARTED', 'Iniciado'
    CLEAN = 'CLEAN', 'Limpio'
    INFECTED = 'INFECTED', 'Amenaza detectada'
    ERROR = 'ERROR', 'Error operativo'


class OrigenIntentoEscaneoDocumento(models.TextChoices):
    ADMIN = 'ADMIN', 'Administrador'
    COMMAND = 'COMMAND', 'Comando de recuperacion'
    AUTOMATIC = 'AUTOMATIC', 'Orquestacion automatica'


class EstadoValidacionIADocumento(models.TextChoices):
    STARTED = 'STARTED', 'Iniciada'
    AUTO_APPROVED = 'AUTO_APPROVED', 'Aceptacion automatica concluyente'
    AUTO_REJECTED = 'AUTO_REJECTED', 'Rechazo automatico concluyente'
    MANUAL_REVIEW = 'MANUAL_REVIEW', 'Requiere revision manual'
    ERROR = 'ERROR', 'Fallo tecnico'


class OrigenValidacionIADocumento(models.TextChoices):
    ADMIN = 'ADMIN', 'Administrador'
    COMMAND = 'COMMAND', 'Comando operativo'
    AUTOMATIC = 'AUTOMATIC', 'Orquestacion automatica'


class MotivoRechazoDocumento(models.TextChoices):
    UNREADABLE = 'UNREADABLE', 'Documento ilegible'
    INCOMPLETE = 'INCOMPLETE', 'Documento incompleto'
    WRONG_DOCUMENT = 'WRONG_DOCUMENT', 'Tipo documental incorrecto'
    EXPIRED = 'EXPIRED', 'Documento vencido'
    DATA_MISMATCH = 'DATA_MISMATCH', 'Datos no coinciden'
    OTHER = 'OTHER', 'Otro motivo controlado'


class EstadoEvidenciaMatricula(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente de revision'
    ACCEPTED = 'ACCEPTED', 'Aceptada'
    REJECTED = 'REJECTED', 'Rechazada'


class TipoEventoParticipante(models.TextChoices):
    CREATED = 'CREATED', 'Creado'
    UPDATED = 'UPDATED', 'Actualizado'


class OrigenCapturaDocumento(models.TextChoices):
    INSTITUTION_API = 'INSTITUTION_API', 'API institucional'
    USER_UPLOAD = 'USER_UPLOAD', 'Carga del usuario'
    CAMERA = 'CAMERA', 'Camara'
    INTERNAL = 'INTERNAL', 'Generado internamente'


class MetodoCalculoFinanciero(models.TextChoices):
    FRENCH_AMORTIZATION = 'FRENCH_AMORTIZATION', 'Anualidad francesa'


class EstadoConfiguracionFinanciera(models.TextChoices):
    DRAFT = 'DRAFT', 'Borrador'
    ACTIVE = 'ACTIVE', 'Activa'
    RETIRED = 'RETIRED', 'Retirada'


class PoliticaRedondeoFinanciero(models.TextChoices):
    COP_PESO_HALF_UP = 'COP_PESO_HALF_UP', 'COP al peso, mitad hacia arriba'


class PoliticaCausacionInteres(models.TextChoices):
    DAILY_30 = 'DAILY_30', 'Prorrateo diario, base comercial de 30 dias'


class PropositoInvitacionContinuacion(models.TextChoices):
    CONTINUE_APPLICATION = 'CONTINUE_APPLICATION', 'Continuar solicitud'


class EstadoInvitacionContinuacion(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Activa'
    CONSUMED = 'CONSUMED', 'Consumida'
    REVOKED = 'REVOKED', 'Revocada'


class TipoEventoInvitacion(models.TextChoices):
    ISSUED = 'ISSUED', 'Emitida'
    REVOKED = 'REVOKED', 'Revocada'
    CONSUMED = 'CONSUMED', 'Consumida'
    DELIVERY_SCHEDULED = 'DELIVERY_SCHEDULED', 'Entrega programada'
    DELIVERY_STARTED = 'DELIVERY_STARTED', 'Entrega iniciada'
    DELIVERY_SENT = 'DELIVERY_SENT', 'Entrega enviada'
    DELIVERY_FAILED = 'DELIVERY_FAILED', 'Entrega fallida'


class CanalEntregaInvitacion(models.TextChoices):
    EMAIL = 'EMAIL', 'Correo electronico'


class EstadoEntregaInvitacion(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente'
    SENDING = 'SENDING', 'En envio'
    SENT = 'SENT', 'Enviada'
    FAILED = 'FAILED', 'Fallida'
    CANCELLED = 'CANCELLED', 'Cancelada'
    SUPERSEDED = 'SUPERSEDED', 'Reemplazada'


class OrigenEntregaInvitacion(models.TextChoices):
    INITIAL = 'INITIAL', 'Inicial'
    AUTOMATIC_RETRY = 'AUTOMATIC_RETRY', 'Reintento automatico'
    MANUAL_REISSUE = 'MANUAL_REISSUE', 'Reemision manual'


class EstadoEnlaceCapturaMovil(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Activo'
    CONSUMED = 'CONSUMED', 'Consumido'
    REVOKED = 'REVOKED', 'Revocado'


class EstadoEntregaCapturaMovil(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente'
    SENDING = 'SENDING', 'En envio'
    SENT = 'SENT', 'Enviada'
    FAILED = 'FAILED', 'Fallida'


class TipoEventoEnlaceCapturaMovil(models.TextChoices):
    ISSUED = 'ISSUED', 'Emitido'
    REVOKED = 'REVOKED', 'Revocado'
    DELIVERY_STARTED = 'DELIVERY_STARTED', 'Entrega iniciada'
    DELIVERY_SENT = 'DELIVERY_SENT', 'Entrega enviada'
    DELIVERY_FAILED = 'DELIVERY_FAILED', 'Entrega fallida'
    CONSUMED = 'CONSUMED', 'Consumido'


class TipoEventoSeguridadFinanciacion(models.TextChoices):
    UNAUTHORIZED_APPLICATION_ACCESS = (
        'UNAUTHORIZED_APPLICATION_ACCESS',
        'Acceso no autorizado a solicitud',
    )
    INVITATION_ACCOUNT_MISMATCH = (
        'INVITATION_ACCOUNT_MISMATCH',
        'Cuenta no coincide con invitacion',
    )
    REASSOCIATION_ATTEMPT = (
        'REASSOCIATION_ATTEMPT',
        'Intento de reasociacion',
    )
    MOBILE_CAPTURE_CONTEXT_MISMATCH = (
        'MOBILE_CAPTURE_CONTEXT_MISMATCH',
        'Contexto movil no autorizado',
    )


class TipoDecisionRevisionEducativa(models.TextChoices):
    APPROVED = 'APPROVED', 'Aprobar expediente y continuar a pagare'
    REJECTED = 'REJECTED', 'Rechazar'
    CORRECTION_REQUESTED = (
        'CORRECTION_REQUESTED',
        'Solicitar correcciones',
    )


class MotivoDecisionRevisionEducativa(models.TextChoices):
    REQUIREMENTS_VERIFIED = (
        'REQUIREMENTS_VERIFIED',
        'Requisitos verificados',
    )
    INCOMPLETE_INFORMATION = (
        'INCOMPLETE_INFORMATION',
        'Informacion incompleta',
    )
    UNREADABLE_DOCUMENT = (
        'UNREADABLE_DOCUMENT',
        'Documento ilegible',
    )
    IDENTITY_MISMATCH = (
        'IDENTITY_MISMATCH',
        'Inconsistencia de identidad',
    )
    GUARDIANSHIP_NOT_VERIFIED = (
        'GUARDIANSHIP_NOT_VERIFIED',
        'Representacion no verificada',
    )
    ENROLLMENT_NOT_VERIFIED = (
        'ENROLLMENT_NOT_VERIFIED',
        'Matricula no verificada',
    )
    OTHER = 'OTHER', 'Otro motivo controlado'


class RequisitoCorreccionEducativa(models.TextChoices):
    STUDENT = 'STUDENT', 'Datos del estudiante'
    GUARDIAN = 'GUARDIAN', 'Datos del tutor'
    STUDENT_ID_FRONT = 'STUDENT_ID_FRONT', 'Identificacion estudiante - frente'
    STUDENT_ID_BACK = 'STUDENT_ID_BACK', 'Identificacion estudiante - reverso'
    GUARDIAN_ID_FRONT = 'GUARDIAN_ID_FRONT', 'Identificacion tutor - frente'
    GUARDIAN_ID_BACK = 'GUARDIAN_ID_BACK', 'Identificacion tutor - reverso'
    INCOME_CERTIFICATE = 'INCOME_CERTIFICATE', 'Certificado de ingresos'
    ENROLLMENT_EVIDENCE = 'ENROLLMENT_EVIDENCE', 'Evidencia de matricula'


class EstadoEntregaCorreoSolicitud(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente'
    SENDING = 'SENDING', 'En envio'
    SENT = 'SENT', 'Enviado'
    FAILED = 'FAILED', 'Fallido'


class EstadoPublicoSolicitud(models.TextChoices):
    RECEIVED = 'RECEIVED', 'Recibida'
    ACTION_REQUIRED = 'ACTION_REQUIRED', 'Requiere accion del solicitante'
    UNDER_REVIEW = 'UNDER_REVIEW', 'En revision'
    APPROVED = 'APPROVED', 'Aprobada; curso autorizado'
    REJECTED = 'REJECTED', 'Rechazada'
    CANCELLED = 'CANCELLED', 'Cancelada'


class TipoArtefactoContractualEducativo(models.TextChoices):
    PROMISSORY_NOTE = 'PROMISSORY_NOTE', 'Pagare educativo'
    ENROLLMENT_FORM = 'ENROLLMENT_FORM', 'Ficha de matricula'


class EstadoArtefactoContractualEducativo(models.TextChoices):
    GENERATED = 'GENERATED', 'Generado'
    SENT_FOR_SIGNATURE = 'SENT_FOR_SIGNATURE', 'Enviado a firma'
    SIGNED = 'SIGNED', 'Firmado'
    CANCELLED = 'CANCELLED', 'Cancelado'


class EstadoProcesoFirmaEducativa(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente de envio'
    SENDING = 'SENDING', 'Enviando'
    SENT = 'SENT', 'Pendiente de firma'
    SIGNED = 'SIGNED', 'Firmado'
    REFUSED = 'REFUSED', 'Firma rechazada'
    FAILED = 'FAILED', 'Envio fallido'
    CANCELLED = 'CANCELLED', 'Cancelado'
    EXPIRED = 'EXPIRED', 'Vencido'


class EstadoEventoWebhookFirmaEducativa(models.TextChoices):
    RECEIVED = 'RECEIVED', 'Recibido'
    PROCESSED = 'PROCESSED', 'Procesado'
    IGNORED = 'IGNORED', 'Ignorado'
    RETRYABLE_ERROR = 'RETRYABLE_ERROR', 'Fallo recuperable'


class EstadoVersionTerminos(models.TextChoices):
    DRAFT = 'DRAFT', 'Borrador'
    PUBLISHED = 'PUBLISHED', 'Publicada'
    RETIRED = 'RETIRED', 'Retirada'
