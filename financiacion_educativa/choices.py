from django.db import models


class EstadoSolicitudFinanciacion(models.TextChoices):
    PENDING_USER_REGISTRATION = 'PENDING_USER_REGISTRATION', 'Pendiente de registro del usuario'
    PENDING_TERMS = 'PENDING_TERMS', 'Pendiente de terminos'
    PENDING_DOCUMENT = 'PENDING_DOCUMENT', 'Pendiente de documento'
    PENDING_GUARDIAN = 'PENDING_GUARDIAN', 'Pendiente de tutor'
    PENDING_MANUAL_REVIEW = 'PENDING_MANUAL_REVIEW', 'Pendiente de revision manual'
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
    ENROLLMENT_EVIDENCE = 'ENROLLMENT_EVIDENCE', 'Evidencia de matricula'
    OTHER_EDUCATIONAL = 'OTHER_EDUCATIONAL', 'Otro documento educativo'
    STUDENT_ID_FRONT = 'STUDENT_ID_FRONT', 'Identificacion del estudiante (anterior)'
    GUARDIAN_ID_FRONT = 'GUARDIAN_ID_FRONT', 'Identificacion del tutor (anterior)'
    OTHER = 'OTHER', 'Otro documento (anterior)'


class EstadoValidacionDocumento(models.TextChoices):
    PENDING = 'PENDING', 'Pendiente de revision'
    APPROVED = 'APPROVED', 'Aceptado'
    REJECTED = 'REJECTED', 'Rechazado'


class EstadoEscaneoDocumento(models.TextChoices):
    PENDING_SECURITY_SCAN = 'PENDING_SECURITY_SCAN', 'Pendiente de escaneo de seguridad'
    SAFE = 'SAFE', 'Sin hallazgos reportados por el escaner'
    BLOCKED = 'BLOCKED', 'Bloqueado por seguridad'


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


class EstadoVersionTerminos(models.TextChoices):
    DRAFT = 'DRAFT', 'Borrador'
    PUBLISHED = 'PUBLISHED', 'Publicada'
    RETIRED = 'RETIRED', 'Retirada'
