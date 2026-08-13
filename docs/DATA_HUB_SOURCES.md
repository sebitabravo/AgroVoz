# Fuentes oficiales y límites de integración

Verificación técnica realizada el **2026-08-13**. “Conectada” significa que
AgroVoz tiene un adaptador o snapshot versionado; no significa que la base
completa de una institución esté copiada en SQLite.

| Fuente | Estado en AgroVoz | Integración | Límite explícito |
|---|---|---|---|
| [ODEPA](https://datos.odepa.gob.cl/es/) | Conectada | Servicio de precios y sync estructurado | El precio se entrega con unidad, mercado, fecha y fuente; el Data Hub no reemplaza el servicio de precios. |
| [Open-Meteo](https://open-meteo.com/en/docs) | Conectada | Clima live con cache | Solo comunas/coordenadas soportadas; no inventa clima para lugares no cubiertos. |
| [Red Agrometeorológica INIA](https://agrometeorologia.cl/) | Conectada | `items-resumen.json` en sync admin; cuenta estaciones y guarda hash | No se ingieren coordenadas ni series completas en la consulta. |
| [Pulso Agroclimático INIA](https://www.inia.cl/2025/06/26/inia-lanza-pulso-agroclimatico-nuevo-boletin-de-monitoreo-de-la-realidad-agricola-y-climatica-nacional/) | `not_connected` | Catálogo documental | La página oficial describe boletines mensuales, pero no se verificó un contrato de descarga machine-readable estable para este producto. |
| [INIA conocimiento agronómico](https://biblioteca.inia.cl/handle/20.500.14001/6706) | Conectada | Snapshots versionados | Solo se verbalizan reglas que tienen fuente y fecha. |
| [INDAP](https://www.indap.gob.cl/plataforma-de-servicios) | Conectada | Snapshots de programas y directorios | Convocatorias y condiciones deben confirmarse con INDAP antes de postular. |
| [IDE Minagri](https://ide.minagri.gob.cl/) / [CIREN API](https://api-ideminagri.ciren.cl/api/validador/valida-coordenada-comuna/) | Conectada | Probe fijo del validador oficial | Verifica servicio y respuesta, pero no descarga todas las capas GIS ni entrega una interpretación territorial automática. |
| [INE Censo Agropecuario](https://www.ine.gob.cl/estadisticas-por-tema/agricultura-y-medio-ambiente/censo-agropecuario) | Conectada | Catálogo oficial de archivos del VIII Censo | Verifica el índice de archivos; no descarga CSV/XLSX/PDF masivos ni presenta el censo como dato actual. |
| [CampoClick](https://www.indap.gob.cl/noticias/ciren-e-indap-lanzan-campoclick-30-con-nuevos-servicios-y-convenios-para-el-mundo) | `not_connected` | Solo catálogo institucional | No se encontró un acceso público autorizado y estable que permita ingerir el directorio sin scraping opaco o copiar datos sin autorización. |

## Operación

- `POST /api/v1/admin/data-hub/sync` carga manifest y snapshots locales sin red.
- `POST /api/v1/admin/data-hub/sync?remote=true` ejecuta los tres
  verificadores remotos con `X-Admin-Key`; cada fuente falla de forma aislada.
- Los verificadores usan endpoints constantes del código, límite de respuesta y
  códigos de error estables. No aceptan URLs del usuario ni persisten los
  payloads remotos.
- La fecha de verificación y el estado de frescura son parte de la respuesta;
  una fuente con error o vencida no se presenta como dato actual.
