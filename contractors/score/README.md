# Score interno read-only para prestadores

Este modulo calcula un score interno preliminar para solicitudes de
contratistas/prestadores de servicios.

## Principios

- El resultado es read-only.
- No aprueba ni rechaza creditos productivamente.
- No consulta DataCredito.
- No crea `Credito` ni `CreditoLibranza`.
- No modifica `ContractorApplication`.
- Pesos, bandas, montos y penalizaciones viven en configuracion versionada.

## Configuracion

La configuracion vigente es `CONFIGURACION_SCORE_PRESTADORES_V1`.

Incluye:

- componentes ponderados: DataCredito, capacidad, comportamiento digital,
  riesgo fraude y referencias;
- componente penalizador: geolocalizacion;
- bandas de score de 0 a 1000;
- decision preliminar read-only;
- monto y plazo sugeridos por banda;
- reglas criticas separadas de las bandas.

Para ajustar pesos, bandas o montos se debe cambiar la configuracion, no el
motor.

## DataCredito

DataCredito queda como `PENDIENTE`. El motor puede calcular un score parcial
con los componentes disponibles, pero marca `requiere_revision_manual=True`
cuando existe informacion pendiente.

## Integracion

`contractors.services.predecision.evaluar_predecision_contratista` llama el
score solo cuando documental, capacidad contractual y riesgo no tienen bloqueos
criticos.

## Pendiente

- persistencia historica del resultado;
- administracion de parametros desde Django Admin;
- integracion real de DataCredito;
- trazabilidad de cambios de configuracion;
- calibracion contra el Excel de score y datos reales.
