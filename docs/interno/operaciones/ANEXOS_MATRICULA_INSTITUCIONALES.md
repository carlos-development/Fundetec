# Anexos de matricula institucionales

Implementacion local, pendiente de revision visual e institucional. No se han
consultado ni modificado expedientes de produccion. JOW-D09312B0 no se modifica.

## Seleccion y conservacion

`ConfiguracionAnexoMatriculaEducativa` relaciona un perfil activo con el UUID
persistente de `Institucion`. No usa nombres, cursos, referencias externas ni
UUID de instituciones escritos en codigo. Configurar una vez por institucion:

| Perfil | Version del anexo | Ficha / paquete |
| --- | --- | --- |
| FUNDETEC_FO_AD_005 | FO-AD-005-V2-EDU-2 | 1 / 4 paginas, contenido anterior |
| JOWALTH_ACADEMY | JOW-MAT-1.0 | 4 / 7 paginas |

No hay perfil predeterminado implicito. Una institucion sin configuracion activa
queda bloqueada antes de generar o enviar un paquete nuevo. Tambien debe
configurarse Fundetec antes de reanudar generacion: conserva su ficha existente,
pero requiere seleccion explicita. Esto evita que un Jowalth no configurado
reciba por error la ficha de Fundetec.

Las tres primeras paginas del paquete mantienen el pagare, carta de instrucciones,
Habeas Data e importes originales. Las cuatro paginas Jowalth se agregan al final.
La ficha independiente usa exactamente esos mismos bytes de anexo. Cada artefacto
registra `anexo_matricula_snapshot` con UUID, perfil y version; mantiene su hash y
archivo privado. El envio verifica tamano y SHA256 y transmite el PDF persistido,
sin regenerarlo. Un reintento reutiliza artefactos y proceso de firma existentes.

Los artefactos anteriores conservan snapshot `{}`: la migracion no los rellena.
Fundetec admite explicitamente sus versiones historicas conocidas sin modificarlas.
Un artefacto Jowalth GENERATED sin snapshot coherente queda para revision; no se
reemplaza ni reenvia automaticamente. SENT_FOR_SIGNATURE/SIGNED mantienen sus archivos y
evidencias. Una configuracion que ya produjo snapshots no permite cambiar perfil
desde Admin; una futura nueva version requiere un cambio revisado. Se permite
desactivarla, lo cual bloquea nuevos envios, no anula firmas ni contratos existentes.

## Referencia y mapeo

Referencia autorizada: `Ficha_Matricula_Jowalth_Academy.pdf`, cuatro paginas,
sin campos AcroForm. SHA256:
`c528f677ea907431a56a7377a716b050810895ca8cb8bff835cae3c4cff1025c`.
Se reconstruye como HTML/PDF versionado; el PDF original y sus metadatos personales
no se incorporan al repositorio. No se copia el autor de la referencia.

| Pagina / campos | Fuente y tratamiento |
| --- | --- |
| 1: numero matricula, periodo, sede | `solicitud.codigo_matricula`, `periodo_academico`, `sede`; vacios si no constan. Sede no se usa para inferir ciudad. |
| 1: fecha matricula | `solicitud.fecha_matricula` real; puede estar vacia antes de firmar. No se inventa la fecha del render. |
| 1: nombres, apellidos, tipo/numero de identidad, nacimiento | Participante STUDENT; fallback a campos declarados de solicitud si no hay rol. El flujo contractual sigue exigiendo estudiante y responsable unicos. |
| 1: asesor, ingreso, expedicion, lugar nacimiento, edad, sexo, estado civil, RH | Vacios. No se extraen datos adicionales de la cedula ni se amplian modelos. |
| 2: direccion, celular, correo | Datos declarados en solicitud. Confirmar con Jowalth si para menores deben recogerse contactos propios adicionales del estudiante. |
| 2: acudiente, documento, parentesco, celular, correo | Participante GUARDIAN, cuando existe. No inferir acudiente en adultos. El tutor sigue siendo deudor del menor en el pagare. |
| 2: EPS, estrato, ocupacion, empresa, barrio, municipio/departamento, fijo | Vacios; no existen fuentes estructuradas suficientes. No usar domicilio del acreedor como residencia. |
| 2: ultimo nivel, institucion previa, grado, ciudad, estudios tecnicos | Vacios. Jowalth debe confirmar su diligenciamiento; los asteriscos de su referencia no constituyen nuevas validaciones del flujo financiero. |
| 3: curso y jornada | `solicitud.nombre_curso`, `jornada`. Se muestran sin marcar automaticamente ninguna opcion del catalogo de la referencia. |
| 3: como conocio, inscripcion, matricula, mensualidad/semestre, descuento, total academico | Vacios. Confirmar desglose y semantica con Jowalth; no confundir valor del plan, capital financiado ni cuota contractual con estas partidas. |
| 3: forma, numero de cuotas, entidad, convenio, medio de pago | Vacios y casillas sin marcar; requieren confirmacion institucional, no se infiere una eleccion por firmar el pagare. |
| 4: lista de documentos adjuntos | Casillas vacias. No certifica ampliacion 150 %, diploma, EPS, foto ni pagos por existir documentos financieros. |
| 4: tratamiento de datos e imagen | Jowalth aprobo conservar ambas casillas visibles y sin marcar. La muestra actual cumple esta decision. No se incorpora su parrafo como clausula, no se agrega autorizacion ni flujo de consentimiento y no se presume aceptacion por firmar el paquete. |
| 4: firmas, nombres/identidades junto a firma, fechas, sello | Completamente sin diligenciar. La firma del paquete no se presenta como firma de cada casilla, de tutor ausente o de representante Jowalth. |

Decision comunicada por Carlos el 2026-11-07: Jowalth aprobo dejar visibles y sin
marcar las casillas de tratamiento de datos y uso de imagen. Se conserva la muestra
y la plantilla actuales; no se requiere agregar un flujo de consentimiento para
este bloque. Esta decision no constituye una autorizacion otorgada por el estudiante.

Pendiente de confirmacion Jowalth: aceptacion del formato reconstruido, catalogo,
ciudad, datos de contacto/membrete oficial, diligenciamiento de campos academicos
obligatorios en su referencia, importes academicos y firmas institucionales.
La decision sobre ambas casillas ya esta resuelta. No se agregan fuentes de datos
ni autorizaciones nuevas.
Las muestras son sinteticas y muestran marca de test; no prueban aceptacion juridica.

## Diagnostico de solo lectura antes de staging

Usar `staging_manage shell` en el servidor SOLO despues de autorizar despliegue.
Sustituir el UUID por el obtenido y verificado en Institucion, nunca buscar por
nombre comercial. La siguiente consulta no imprime PII, tokens ni rutas privadas:

```python
from uuid import UUID
from django.db.models import Count
from instituciones.models import Institucion
from financiacion_educativa.models import ArtefactoContractualEducativo

institucion_id = UUID(input('UUID institucional verificado: ').strip())
assert Institucion.objects.filter(pk=institucion_id).exists()
qs = ArtefactoContractualEducativo.objects.filter(
    solicitud__institucion_id=institucion_id,
    estado__in=['GENERATED', 'SENT_FOR_SIGNATURE', 'SIGNED'],
).order_by('solicitud_id', 'tipo', 'numero_version')
print(list(qs.values('estado').annotate(total=Count('pk'))))
for a in qs.iterator():
    print(a.solicitud_id, a.pk, a.tipo, a.estado, a.numero_version,
          a.version_plantilla, a.anexo_matricula_snapshot, a.hash_sha256,
          'REVISION_HISTORICA' if not a.anexo_matricula_snapshot else 'VERSIONADO')
```

Separar GENERATED historicos de SENT_FOR_SIGNATURE/SIGNED. Ninguna categoria autoriza regenerar,
cancelar, reenviar o sustituir automaticamente. Incluir JOW-D09312B0 en revision
documental operativa sin alterar su estado, pagar firmado o evidencias. Si no esta
disponible la nueva columna antes de migrar, omitir snapshot en el informe previo
y considerar todos los resultados como historicos; repetir despues de migrar.

## Preparacion de staging (no ejecutada)

1. Revisar diff y cuatro paginas con Jowalth. Registrar UUID real de cada
   institucion y perfil aprobado. Revisar informe historico anterior. Este trabajo
   esta en rama separada desde origin/main; no incluye la anulacion local pendiente.
2. Antes de un despliegue autorizado: respaldo de PostgreSQL y archivos privados;
   detener `fundetec-staging-educational-worker.service`. La aplicacion puede
   mantenerse para consultas. Pausar firma con la configuracion staging/test ya
   existente `FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED=True` y reiniciar los
   procesos que la carguen; no realizar llamadas de prueba a proveedores reales.
3. Desplegar el SHA publicado de la rama de anexos que se apruebe, sin fusionar
   a main, usando el runbook vigente.
   No hay dependencias nuevas. Validar e instalar SOLO la migracion aditiva:

```bash
staging_manage migrate --plan
staging_manage sqlmigrate financiacion_educativa 0030
staging_manage migrate financiacion_educativa 0030
staging_manage check
```

   Migracion exacta: `0030_anexos_matricula_institucionales`, depende de
   `0029_recaudos_breb`, instituciones `0003` y usuario intercambiable. Agrega
   configuracion/snapshot y choices diagnosticos; no cambia filas historicas ni
   recalcula condiciones. Hay otra 0030 en la rama WIP de anulacion: antes de
   integrar esa rama se debe renumerar/rebasar su migracion, no desplegar dos hojas
   incompatibles ni usar --fake.
4. En Django Admin, otorgar `financiacion_educativa.gestionar_anexos_matricula`
   solo al responsable autorizado (superusuario conserva acceso). Crear una
   configuracion por UUID: Fundetec -> FUNDETEC_FO_AD_005; Jowalth -> JOWALTH_ACADEMY.
   Revisar perfil/version antes de activar. El actor y fecha quedan registrados;
   LogEntry de Admin conserva la edicion. No borrar configuraciones.
5. Probar con nueva solicitud sintetica y proveedores simulados en una base
   aislada: Fundetec 4/1, Jowalth 7/4, falta de perfil bloquea, hashes coinciden,
   reintento no regenera. No usar un expediente real para certificar el anexo.
6. Antes de reactivar el worker, ejecutar este prechequeo de solo lectura despues
   de migrar y configurar Admin. Revisa todas las instituciones, incluida Fundetec;
   tambien detecta perfiles desactivados o no reconocidos. Solo imprime UUID y
   estado de activacion, sin datos personales. No llama a proveedores ni modifica filas:

```python
from instituciones.models import Institucion
from financiacion_educativa.models import ConfiguracionAnexoMatriculaEducativa
from financiacion_educativa.services.anexos_matricula import PERFILES

con_perfil = ConfiguracionAnexoMatriculaEducativa.objects.filter(
    activa=True, perfil__in=tuple(PERFILES),
).values('institucion_id')
sin_perfil = Institucion.objects.exclude(pk__in=con_perfil).order_by('pk')
total = sin_perfil.count()
print('INSTITUCIONES_SIN_PERFIL_ACTIVO_VALIDO=', total)
for institucion_id, activa in sin_perfil.values_list('pk', 'activa').iterator():
    print('INSTITUCION_ID=', institucion_id, 'ACTIVA=', activa)
assert total == 0, 'NO REACTIVAR WORKER: resolver perfiles institucionales en Admin.'
```

   Si aparecen resultados, mantener el worker detenido y la firma pausada;
   verificar cada UUID y configurar su perfil aprobado desde Admin. No asignar
   Fundetec como alternativa ni modificar solicitudes o historicos. Repetir el
   prechequeo hasta obtener cero pendientes antes de continuar.
7. Solo con revision aprobada y prechequeo sin pendientes: retirar pausa de firma y reiniciar worker. No
   reencolar historicos masivamente. Verificar Operaciones: errores
   ENROLLMENT_ANNEX_NOT_READY, ENROLLMENT_ANNEX_GENERATION_FAILED o
   CONTRACT_FILE_INTEGRITY_FAILED quedan trazables y exigen correccion/revision.

Rollback: detener worker y mantener pausa de firma; desactivar perfil afectado.
No borrar la migracion, snapshots ni archivos, ni volver a una ficha alternativa.
No revertir el esquema si ya existen artefactos. La vuelta de codigo solo se
considera tras revisar que el codigo anterior no reenvie historicos Jowalth;
mantener envios detenidos hasta una version compatible aprobada.

## Validacion local

```powershell
$env:DJANGO_LOAD_DOTENV='false'
$env:DEPLOYMENT_ENVIRONMENT='test'
$env:DEBUG='True'
$env:USE_SQLITE='false'
$env:DATABASE_URL='sqlite:///:memory:'
& C:\.vscode\Fundetec\venv\Scripts\python.exe manage.py test financiacion_educativa.tests.test_anexos_matricula financiacion_educativa.tests.test_artefactos_contractuales financiacion_educativa.tests.test_firma_educativa --noinput
```

Para exportar muestras, establecer `EDU_ANNEX_SAMPLES_DIR` a una carpeta temporal
fuera del repositorio. La prueba de siete paginas exporta solo sus fixtures
sinteticos y PNG de cada pagina. Prueba concurrente requiere PostgreSQL aislado;
no usar una base compartida. Nunca adjuntar documentos reales al repositorio.

### Resultado del 2026-11-07

- Base: c4fe3b8025099725093eabafd42826919c32899a (origin/main).
- PostgreSQL 16.13 local temporal, sin datos ni credenciales de servidores.
- Regresion final: test_anexos_matricula, test_artefactos_contractuales y
  test_firma_educativa: 43/43 OK, 95.653 s, cero omisiones. Incluye dos conexiones
  independientes generando exactamente dos artefactos y un proceso de firma.
- Seleccion ampliada con test_orquestacion_automatica y test_cola_automatizacion:
  80/81 OK, 151.311 s, cero omisiones. Un fallo preexistente:
  test_endpoint_progreso_es_privado_persistente_y_sin_detalles_internos no acepta
  terminos en su fixture y obtiene NOT_STARTED en vez de QUEUED. Reproducido
  tambien en un worktree limpio del SHA base: 1 FAIL, 0.634 s. Su correccion ya
  existe en la rama WIP de anulacion; no se mezclo con este bloque.
- Migracion aplicada solo en PostgreSQL temporal. check: 0 incidencias;
  makemigrations --check --dry-run: No changes detected; migrate --plan sin
  operaciones pendientes. sqlmigrate 0030 revisado: JSONB aditivo y nueva tabla,
  dos AlterField de choices sin SQL material. diff --check correcto.
- PDFs de ficha (4 paginas) y paquete (7), mas PNG por pagina, generados con
  fixtures sinteticos y revisados localmente. Pendiente aprobacion visual de
  Jowalth y definicion de los campos/espacios institucionales restantes antes de
  activar. La permanencia de ambas casillas visibles y sin marcar ya fue aprobada
  por Jowalth; no requiere cambios de plantilla ni un flujo de consentimiento.
