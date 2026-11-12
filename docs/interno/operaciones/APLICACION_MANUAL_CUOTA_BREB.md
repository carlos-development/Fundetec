# Aplicar manualmente un recaudo conciliado

Estado actual, 2026-11-11: Carlos confirma `82028d2` desplegado en staging,
con 0031 y 0032 aplicadas. Operaciones muestra las cuotas 1 y 2 pagadas,
el estudiante ve ambos pagos aplicados y el acceso directo a la evidencia
bancaria privada devuelve 403. Los duplicados solo se validaron localmente,
no en staging.
Es un antecedente aportado por el responsable, no una inspeccion remota de
esta revision. Las secciones historicas de instalacion/validacion siguientes
no indican que deban repetirse migraciones ya aplicadas. El cierre y plan
vigentes se encuentran al final de este documento.

Primer alcance de la Fase 2B. La migración `0031_aplicacion_manual_cuota` crea
un registro por ingreso y por cuota. No aplica movimientos existentes por lote.

En Operaciones → Recaudos, abre el reporte ya conciliado. Verifica en el banco
la referencia y la fecha efectiva, y en la vista previa la cuota que el pagador
quiso cubrir. Confirma solo si se trata de la primera cuota pendiente del
calendario firmado y el monto la cubre exactamente. El desglose se toma del
calendario firmado; el operador no digita importes ni fechas. Si no hay
coincidencia, deja el ingreso pendiente de aplicación y escala el caso.

La acción requiere acceso al dashboard, consulta de recaudos y el permiso
`financiacion_educativa.aplicar_cuotas_educativas`. Conciliar por sí solo no da
permiso de aplicar. El reporte y el detalle de la obligación muestran el
resultado. El capital pendiente mostrado se deriva de las aplicaciones
registradas; no es una liquidación a una fecha de corte.

No se admiten parciales, abonos a capital, pagos totales, excedentes ni reversos.
No se modifica el calendario, la firma ni el estado de la solicitud. No
confirmes una aplicación si no puedes verificar la cuota: esta primera entrega
no tiene una operación de reversión. Un error se escala a Operaciones sin
editar ni eliminar el registro.

## Validación antes de activar

1. Confirmar que el entorno de pruebas usa PostgreSQL y una base de pruebas
   aislada. Ejecutar `manage.py test financiacion_educativa.tests.test_aplicacion_cuotas`
   y exigir que pasen las dos pruebas de concurrencia sin omisiones.
2. Revisar el plan de migración y aplicar `0031_aplicacion_manual_cuota` en
   staging. Con un recaudo sintético, probar vista previa, confirmación,
   reintento y consulta del resultado.
3. Produccion requiere autorizacion independiente posterior a staging y respaldo.
   No aplicar el caso real en esta entrega. Primero ejecutar el prechequeo de solo
   lectura siguiente; ni el despliegue ni la migracion autorizan aplicar dinero.
4. Comprobar que hay una aplicación, la cuota aparece pagada y el estudiante
   ve el resultado en su reporte. No volver a conciliar ni pedir otro pago.

## Controles del incremento local (2026-11-11)

Base origin/main `dafe477` (incluye anexos 0030). Anulacion fuera de esta rama.
El parche se adapto: confirmar la intencion del estudiante es una casilla requerida
en servidor y registrada como `cuota_verificada` en la aplicacion inmutable.
Recibido, reportado y cuota deben coincidir exactamente. Diferencias o dudas
requieren escalamiento, aunque ya exista una justificacion de conciliacion.

La vista previa firmada vincula operador, reporte, conciliacion, cuota, importes,
vencimiento y fecha bancaria. Vence a los 30 minutos. Un cambio exige recargar;
no permite sustituir otra cuota. Doble clic con vista valida devuelve el mismo
registro. No compartir ni registrar el token. Confirmacion solo por POST + CSRF.

Servicio y vista exigen usuario activo con estos tres permisos independientes:
`financiacion_educativa.acceder_dashboard_operativo`,
`financiacion_educativa.consultar_recaudos_educativos` y
`financiacion_educativa.aplicar_cuotas_educativas`. Ni is_staff ni conciliar bastan.
Migrate crea el permiso, pero no lo asigna a usuarios/grupos. Configurarlo desde
Admin. Obligaciones conserva su permiso propio de consulta de solicitudes.
Aplicador y conciliador pueden ser diferentes: no duplicar la evidencia bancaria.

Transaccion y locks: solicitud, reporte, conciliacion, soportes ordenados por PK
y cuota, solo filas base. Revalidacion SAFE e integridad de ambos soportes; no
llama al antivirus ni a proveedores. Unicidad DB por ingreso y cuota, FK PROTECT
y check de capital positivo, interes no negativo y verificacion verdadera.
Las aplicaciones no admiten save/update/bulk_update/bulk_create/delete posteriores.

No se modifica contrato, fotografia, calendario, conciliacion, autorizacion ni
financial_terms. Incluso al aplicar todas las cuotas, no asigna PAID ni cierra
deuda. No calcula ni condona mora o liquidaciones retroactivas. La fecha bancaria
es trazabilidad, no una liquidacion a corte. Sin reparto, notificaciones ni plan
PDF nuevo. Se mantiene el gate FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED; sin variables,
dependencias o archivos estaticos nuevos.

## Prechequeo de solo lectura del caso real

NO ejecutado. Falta UUID inequivoco del reporte real; copiarlo desde Operaciones,
no buscar por nombre o importe. No confundir con JOW-D09312B0 ni con anulacion.
Con codigo/migracion instalados, abrir `staging_manage shell` y pegar este bloque
reemplazando solo el UUID. En produccion usar su wrapper autorizado, nunca conectar
staging_manage a otra base. No muestra PII, referencia bancaria, ruta ni token.

```python
from uuid import UUID
from django.db import connection, transaction
from django.core.exceptions import ValidationError
from financiacion_educativa.models_recaudos import ReporteRecaudoEducativo, AplicacionCuotaEducativa
from financiacion_educativa.services.aplicacion_cuotas import previsualizar_cuota

reporte_id = UUID('SUSTITUIR_POR_UUID_DEL_REPORTE_CONCILIADO')
assert connection.vendor == 'postgresql'
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
    reporte = ReporteRecaudoEducativo.objects.get(pk=reporte_id)
    ingreso = getattr(reporte, 'conciliacion', None)
    print('REPORTE_UUID=', reporte.pk, 'SOLICITUD_UUID=', reporte.solicitud_id)
    print('CONCILIADO=', ingreso is not None, 'PROPOSITO=', reporte.proposito)
    aplicaciones = AplicacionCuotaEducativa.objects.filter(conciliacion__reporte=reporte)
    print('APLICACIONES_PREVIAS=', aplicaciones.count())
    if aplicaciones.exists():
        print('ELEGIBLE_PARA_NUEVA_APLICACION=False; conservar aplicacion existente')
    else:
        try:
            cuota = previsualizar_cuota(reporte)
        except ValidationError as error:
            print('ELEGIBLE_TECNICAMENTE=False; MOTIVO=', '; '.join(error.messages))
        else:
            print('ELEGIBLE_TECNICAMENTE=True; requiere verificar intencion del estudiante')
            print('CUOTA=', cuota.numero, 'VENCIMIENTO=', cuota.fecha_vencimiento)
            print('RECIBIDO=', ingreso.monto_recibido, 'FECHA_BANCARIA=', ingreso.fecha_efectiva)
            print('CAPITAL=', cuota.capital, 'INTERES=', cuota.interes, 'TOTAL=', cuota.valor_cuota)
```

Elegibilidad tecnica NO autoriza aplicar el pago. Requiere verificacion humana
de intencion, banco y autorizacion operativa expresa posterior. Usar la accion
oficial de interfaz, no shell ni cambios manuales de estado.

## Liberacion de staging (no ejecutada)

1. Revisar diff/SHA aprobado. No hay commit/push de este bloque ni mezcla con WIP
   de anulacion. Respaldar PostgreSQL/archivos privados segun runbook, verificar
   arbol del servidor limpio e instalar solo el release autorizado. Sin otorgar
   permiso aun, sin llamadas a proveedores ni envios reales.
2. Revisar plan y migracion. Si existe otra 0031 incompatible, detenerse: no usar
   --fake ni sobrescribirla. Ejecutar solo tras autorizacion de despliegue:

```bash
staging_manage showmigrations financiacion_educativa
staging_manage migrate --plan
staging_manage sqlmigrate financiacion_educativa 0031_aplicacion_manual_cuota
staging_manage migrate financiacion_educativa 0031_aplicacion_manual_cuota
staging_manage check
systemctl restart fundetec-staging.service
systemctl is-active fundetec-staging.service
curl --fail --silent --show-error https://staging-api.aprobado.com.co/health/
```

3. En PostgreSQL de pruebas separado (nunca base compartida), proveedores simulados,
   correo locmem y automatizacion/IA apagadas, ejecutar:

```text
python manage.py test financiacion_educativa.tests.test_aplicacion_cuotas financiacion_educativa.tests.test_recaudos_breb financiacion_educativa.tests.test_dashboard_obligaciones --noinput
```

4. Asignar permisos al operador de pruebas y usar contrato/reporte sinteticos.
   Comprobar vista previa, checkbox, doble POST, operadores simultaneos, diferencia,
   cuota ajena, caducidad y resultado en Obligaciones y cuenta del estudiante.
5. Prechequeo real solo con permiso de consulta, conservando resultados en expediente
   operativo privado. Si bloquea, no duplicar reporte/conciliacion. La aplicacion
   real queda pendiente de autorizacion posterior. No se accedio al servidor.

Rollback: retirar permiso de aplicacion, conservando registros. No bajar a 0030
ni borrar tabla/aplicaciones. Sin aplicaciones puede revertirse codigo al release
previo aprobado conservando esquema. Si existen aplicaciones, mantener codigo
compatible que las muestre; no volver a una UI que las oculte o presente pendientes.
Cerrar acceso y preparar correccion revisada: no hay reverso automatico.

## Validacion local ejecutada

- Base: `dafe477288690559d52819551531d91638ff253d`, rama separada
  `feat/aplicacion-manual-cuota-breb-20261111`. WIP de anulacion intacto.
- PostgreSQL 16.13 temporal aislado, sin bases/credenciales/datos de servidores.
- Seleccion aplicacion + recaudos BRE-B + obligaciones + UX: 120 pruebas OK,
  229.528 s, cero omisiones, incluidas dos carreras de aplicacion y cinco de recaudos.
- Tras precisar el mensaje de vista desactualizada: modulo completo de aplicacion,
  21 pruebas OK, 72.342 s, cero omisiones; ambas carreras repetidas correctamente.
  Las selecciones se solapan; no son 141 pruebas distintas ni la suite completa.
- check: 0 issues. makemigrations --check --dry-run: No changes detected.
  migrate --plan final: No planned migration operations. 0031 aplicada solo en la
  base temporal; SQL revisado (tabla aditiva, dos UNIQUE, check y FKs).
- diff --check y sintaxis del prechequeo documentado: correctos. Sin JavaScript nuevo.
- Caso real no consultado ni aplicado. Pendiente conocer UUID exacto, validar
  elegibilidad e intencion humana, revisar entrega y autorizar liberacion en staging.

## Extension: ingreso recibido sin reporte del estudiante

En el detalle de Obligaciones, la accion **Registrar pago recibido** permite al
responsable autorizado registrar directamente el movimiento bancario. Requiere
acceso a Operaciones, consulta de recaudos y el nuevo permiso independiente
`financiacion_educativa.registrar_pagos_recibidos_educativos`. No concede permiso
de aplicar ni de conciliar reportes del pagador. Asignarlo explicitamente en Admin.

El modelo de registros de recaudo se reutiliza con `origen=OPERATIONS`, usuario
real en `reportado_por` y en la conciliacion, fecha de registro, importe, fecha
efectiva, referencia normalizada, cuenta y evidencia bancaria propia SAFE con
integridad verificada. No se crea un comprobante del estudiante ni se simula una
captura: `comprobante` y `receptor` quedan nulos. No se exige un QR activo para
contabilizar que el banco ya recibio el ingreso. La cuenta debe existir en la
configuracion bancaria; no se permite crear cuentas desde este formulario.

La evidencia bancaria puede subirse desde la misma pantalla y debe esperar al
escaneo existente. No se sustituye por el QR o un comprobante del pagador. En el
registro se puede mantener la finalidad Pendiente de identificar (UNSPECIFIED).
Solo la posterior confirmacion explicita de **Aplicar a cuota** acredita que se
identifico la cuota; no modifica retroactivamente esa declaracion inicial. Abonos
o pago total explicitos permanecen fuera de la aplicacion de cuotas.

Registrar y aplicar son transacciones distintas. Un monto diferente, una finalidad
no soportada o la falta de certeza humana dejan el ingreso conservado y sin aplicar.
La aplicacion reutiliza la vista firmada y la confirmacion de este procedimiento,
sin escribir en el calendario, firma, autorizacion o financial_terms.

Ambos origenes comparten Cuenta + referencia bancaria unica y el mismo bloqueo de
cuenta. Primero se bloquea la obligacion, luego evidencia, luego cuenta; nunca se
toma el bloqueo de cuenta antes del archivo compartido con el flujo anterior.
Si existe el movimiento, registrar redirige al recaudo original sin crear otro.
Conciliar un reporte posterior tambien redirige al original; el reporte posterior
se conserva sin conciliar, no se borra ni se reasigna silenciosamente. Una clave
idempotente repetida con datos distintos falla. No se fusionan expedientes.

Los listados y el detalle identifican el origen. Operaciones ve el usuario real;
el estudiante solo ve el ingreso de su propia obligacion, sin evidencia bancaria
privada ni identidad interna del operador. Descargar un comprobante inexistente
devuelve 404. Las filas y referencias anteriores permanecen inmutables.

### Migracion y staging de la extension

La `0031_aplicacion_manual_cuota` publicada NO se modifica. La nueva
`0032_ingreso_operaciones` depende de ella: agrega origen y permiso, admite nulos
solo para el origen administrativo mediante un CHECK y agrega la finalidad neutral.
Los registros anteriores reciben PAYER para conservar el flujo previo; no se
cambia su usuario, evidencia, importe, referencia, conciliacion ni aplicacion.

Antes de desplegar: respaldo PostgreSQL y archivos privados, revisar plan/SQL,
confirmar 0031 aplicada y que 0032 no colisiona con otra migracion. Tras autorizacion:

```bash
staging_manage showmigrations financiacion_educativa
staging_manage migrate --plan
staging_manage sqlmigrate financiacion_educativa 0032_ingreso_operaciones
staging_manage migrate financiacion_educativa 0032_ingreso_operaciones
staging_manage check
systemctl restart fundetec-staging.service
```

No asignar el permiso real durante la instalacion. Validar en PostgreSQL aislado:
`test_ingreso_operaciones`, `test_aplicacion_cuotas`, `test_recaudos_breb`,
`test_recaudos_ux` y `test_dashboard_obligaciones`, con proveedores simulados.
Probar entrada sin reporte/comprobante, tres carreras del nuevo registro, doble
origen del mismo movimiento, permisos separados y aplicacion posterior. Despues,
probar interfaz con datos sinteticos. El caso real requiere otra autorizacion.

Para frenar la funcion, retirar el nuevo permiso. No revertir 0032 ni codigo que
asuma comprobante/receptor obligatorios si ya hay ingresos OPERATIONS. No borrar
ingresos, conciliaciones o aplicaciones para permitir una reversa de esquema.

### Consulta previa sin reporte

Solo con autorizacion de lectura, en el shell del ambiente correspondiente,
puede verificarse la unicidad antes de registrar. No ejecutar registrar_pago_recibido
desde shell ni confundir este diagnostico con permiso para aplicar el dinero.
La referencia se introduce sin eco y no se imprime. El resultado solo contiene UUIDs:

```python
from getpass import getpass
from uuid import UUID
from django.db import connection, transaction
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services.recaudos import referencia_bancaria

cuenta_id = UUID(input('UUID de cuenta receptora verificada: ').strip())
referencia = referencia_bancaria(getpass('Referencia bancaria (no se imprime): '))
assert connection.vendor == 'postgresql'
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
    existentes = list(ConciliacionRecaudoEducativo.objects.filter(
        cuenta_id=cuenta_id, referencia_verificada=referencia,
    ).values_list('reporte_id', flat=True))
    print('MOVIMIENTOS_EXISTENTES=', len(existentes), 'RECAUDOS_UUID=', existentes)
```

Si existe movimiento, abrirlo desde Recaudos; no crear otro ni volver a conciliar.
Si no existe, aun se requiere verificar obligacion, banco, evidencia y permisos.

Validacion de esta extension: PostgreSQL 16.13 temporal aislado, 133 pruebas de
regresion OK (195.968 s) incluyendo tres carreras nuevas y las siete anteriores.
Tras completar pruebas de atribucion, CSRF y presentacion, 20 pruebas focalizadas
OK (42.961 s), con las tres carreras nuevas repetidas y sin omisiones. Selecciones
solapadas; no es una ejecucion de toda la suite. Check sin incidencias y
makemigrations sin cambios pendientes; 0032 aplicada solo en la base temporal y
SQL revisado. 0031 conserva el hash Git `8d91a1221b6ca435861262a609f945f1d781d457`.
No hubo registros reales, llamadas a proveedores, commit, push o despliegue.

## Cierre del recorrido sin reporte previo y plan de liberacion

Base funcional candidata: `82028d2dc26035a36779e63ec905c34a2087193d`.
La evidencia nueva de esta revision es local y sintetica, no una repeticion de
la operacion bancaria real ni una certificacion de datos de staging. El incremento
de cierre solo agrega cobertura y actualiza este procedimiento y el roadmap;
no modifica servicios, permisos, interfaz ni las migraciones 0031/0032.

La prueba integrada
`IngresoOperacionesTests.test_recorrido_sin_reporte_repite_registro_y_aplicacion_sin_filtrar_evidencia`
ejecuta las vistas de Django y renderiza sus templates con este recorrido:

1. Obligacion firmada/autorizada sintetica, sin reporte previo del estudiante.
2. Subida de evidencia desde Registrar pago recibido. PENDING bloquea crear el
   ingreso; el antivirus simulado limpio habilita SAFE. Sin proveedores reales.
3. Registro con cuenta, monto exacto, fecha, referencia y evidencia del operador.
   Sin comprobante ni reporte ficticio del estudiante. Todavia no hay cuota pagada.
4. Mismo POST y nueva clave con la referencia normalizada: mismo recaudo.
5. Vista previa y confirmacion explicita: una aplicacion a la primera cuota.
   Repetir el POST y volver a registrar el movimiento no crea otra aplicacion,
   reporte o conciliacion ni avanza silenciosamente a la cuota siguiente.
6. Operaciones muestra Pagada. La cuenta propietaria muestra Pago aplicado a la
   cuota e Ingreso registrado por Operaciones; no muestra actor, referencia,
   identificador de evidencia, ruta privada ni enlace operativo del soporte.
   Intentar descargarlo directamente retorna 403; el comprobante inexistente,
   incluso agregando banco=1, retorna 404. Otra cuenta no puede abrir el reporte.
7. Calendario firmado, conciliacion original, APPROVED, autorizacion y
   financial_terms se conservan. Solo se acredita la aplicacion persistida.

La confirmacion al estudiante de este alcance es la consulta del resultado en
su portal. No se implementan ni envian correos nuevos. Notificaciones automaticas,
reparto y PDF del plan siguen siendo bloques posteriores.

### Reproduccion local aislada

Con PostgreSQL temporal, proveedores simulados y sin cargar .env compartidos:

```text
python manage.py test financiacion_educativa.tests.test_ingreso_operaciones financiacion_educativa.tests.test_aplicacion_cuotas --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

Exigir cero omisiones: tres carreras de registro (incluido el cruce con el reporte
del estudiante y la unicidad global) y dos de aplicacion (mismo ingreso/misma cuota).
No sustituir este resultado por SQLite ni por ejecuciones historicas.

Resultado actual (2026-11-11): PostgreSQL 16.13 temporal aislado; ambos modulos,
39 pruebas OK en 66.391 s, cero omisiones, incluidas las cinco concurrencias y
el recorrido integrado anterior. Se ejecutaron las vistas con Django TestClient
y templates reales; no es una prueba de navegador movil ni una consulta remota.
Los soportes y contratos son sinteticos; antivirus simulado, correo locmem y
sin llamadas a bancos, ZapSign u otros proveedores. Se elimina el contenedor
y su base temporal al finalizar. No se reutilizaron resultados anteriores.

### Evidencia confirmada en staging por Carlos

Sobre `82028d2`, con 0031 y 0032 aplicadas, el responsable confirma:

- Operaciones muestra las cuotas 1 y 2 como pagadas.
- Desde la cuenta del estudiante se ven ambos pagos aplicados.
- El acceso directo a la evidencia bancaria privada devuelve HTTP 403.

Esta comprobacion fue comunicada por Carlos, no ejecutada remotamente por el
agente. No se incluyen datos personales, referencias bancarias ni documentos.
Repeticion del registro, doble aplicacion, unicidad e idempotencia concurrente:
verificadas en las 39 pruebas locales PostgreSQL; NO verificadas en staging
en este cierre. Falta repetir alli el ensayo de duplicados con datos sinteticos
si se exige como condicion de liberacion. No utilizar el pago real para probarlo.

Las comprobaciones finales locales fueron: check sin incidencias, makemigrations
--check --dry-run sin cambios, migrate --plan sin operaciones pendientes y
git diff --check correcto. El contenedor PostgreSQL temporal fue eliminado.

### Liberacion pendiente, sin ejecutar

1. Revisar el diff de cierre y fijar el SHA candidato aprobado. El codigo funcional
   sigue en 82028d2; este cierre versiona cobertura/documentacion en la misma
   rama y se presenta mediante PR a main, sin fusion ni despliegue automaticos.
   No mezclar anulacion, cambios de firma por correo ni redisenos.
2. En staging, mediante el acceso autorizado del responsable, confirmar sin escribir:

```bash
cd /var/www/fundetec-staging/current
sudo -u fundetec-staging git rev-parse HEAD
sudo -u fundetec-staging git status --short
staging_manage showmigrations financiacion_educativa
staging_manage migrate --plan
staging_manage check
systemctl is-active fundetec-staging.service
curl -fsS --max-time 15 https://staging-api.aprobado.com.co/health/
```

   Exigir arbol limpio, HEAD aprobado, 0031/0032 [X] y ausencia de migraciones
   inesperadas. No ejecutar migrate hacia 0031: ya esta superada por 0032.
   No es necesario reinstalar staging para cambiar solo cobertura/documentacion.
3. El resultado visible de cuotas 1/2 y el 403 ya fueron confirmados por Carlos.
   Con cuentas y obligacion sinteticas, completar el ensayo pendiente de
   repeticion de registro/aplicacion en interfaz, conservando el mismo movimiento
   de prueba. Mantener permisos independientes de registrar/aplicar. No usar el
   caso real para este ensayo ni alterar sus evidencias.
4. Antes de produccion: autorizacion separada de release, SHA exacto, revision del
   diff contra el HEAD productivo, respaldo verificable de PostgreSQL y archivos
   privados y plan/SQL de las migraciones que realmente falten. No suponer las
   rutas, HEAD o estado de migraciones productivos a partir de staging. Confirmar
   perfil contractual, cuenta receptora y escaner existentes; no cambiar flags
   de automatizacion, IA, correo o firma para liberar esta funcion.
5. Desplegar solo tras esa autorizacion y mediante el procedimiento productivo
   vigente. Asignar en Admin los permisos especificos al responsable designado,
   diferenciando registrar, conciliar y aplicar. Mantener el caso real sin operar.
   El despliegue NO autoriza registrar/aplicar el pago real: requiere verificar
   movimiento existente, cuota/intencion y autorizacion operativa expresa aparte.
6. Para detener el uso, retirar permisos de registro/aplicacion. Conservar 0031,
   0032, ingresos, evidencias y aplicaciones. No revertir a codigo que requiera
   comprobantes del pagador en ingresos OPERATIONS o esconda aplicaciones. Si
   existen aplicaciones, conservar lectura compatible y escalar cualquier error;
   no existe reverso financiero en este alcance.
