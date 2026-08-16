# Auditoría de claims en Discussions

Estado: **REVISADO Y PUBLICABLE**.
Fecha de revisión: 2026-08-16.
Autorización: el owner autorizó explícitamente publicar los comentarios
individuales y resolver/cerrar los artefactos que tengan evidencia suficiente.

## Criterio

Cada Discussion se contrasta con el repositorio actual. Estados permitidos: implementado localmente, gate apagado, piloto pendiente, operación real no verificada. Tests, mocks y configuración no prueban piloto productivo.

## Hallazgos consolidados

- **Tests y CI:** verificados localmente: 2265 tests backend passed, 3 skipped,
  cobertura 86,29%, 26 tests de landing passed; Ruff y mypy limpios. No
  equivale a 100% operativo en producción.
- **WhatsApp real:** Open-WA, webhook y mocks existen; sesión QR, entrega real y piloto no verificados en este documento. #214 sigue siendo el gate E2E.
- **Visión:** endpoint, panel y servicio existen; modelo local provisionado en esta sesión, gates Docker dev activables. No equivale a precisión clínica/agronómica ni operación productiva.
- **Privacidad/legal:** existen controles técnicos y documentos; revisión jurídica formal y cumplimiento de Ley 21.719 no están acreditados.
- **Latencia/WER:** sin benchmark reproducible en hardware objetivo y sin muestras rurales auditadas, deben declararse metas o mediciones locales, no garantías.
- **Litestream/backup:** no afirmar respaldo operativo continuo sin despliegue y restauración verificables.
- **B2G/piloto:** modelo y documentación existen; contrato, usuarios, operación y resultados de piloto no están acreditados.
- **Calendario #244:** el corpus mantiene 22/79 reglas verificables y falla
  cerrado fuera de cobertura. La biblioteca vigente de Plan Predial lista 33
  fichas; no se inventan las 57 restantes ni se convierten meses de la matriz
  ODEPA 2017 en ventanas específicas para Traiguén.
- **Directorio #246:** la política local ahora falla cerrado cuando la fuente
  no tiene fecha válida, supera 180 días o el snapshot supera 90 días; los
  contactos se muestran solo con vigencia comprobable.

## Borradores de comentarios

Los siguientes comentarios fueron revisados por el owner y se deben publicar
uno por uno. Cada uno enlaza esta auditoría y corrige claims históricos sin
borrar la Discussion.

### #265 - Escalado y BD

> Actualización de estado: esta Discussion es un RFC histórico. SQLite sigue siendo decisión vigente para MVP, pero Litestream, backups restaurables, throughput y escalado productivo no están operados/verificados en este repositorio. El piloto CREA INACAP no se concretó para este proyecto y la infraestructura NAS/VPS no constituye evidencia actual. No presentar metas de latencia como mediciones productivas.

### #207 - Auditoría completa

> Actualización de estado: la suite local actual está verde (2265 tests backend passed, 3 skipped; 26 tests de landing passed), pero eso no prueba WhatsApp autenticado, Open-WA operativo, piloto, hardware 1 vCPU/4 GB ni cumplimiento legal. #214 queda como gate de E2E real. Claims de "100% operativo" deben leerse como snapshot local histórico.

### #137 - Gap de proyecto de título

> DRAFT: Comparación histórica. El repositorio ganó tests, CI, Data Hub, documentación y controles de privacidad, pero la evaluación académica y el piloto real siguen siendo asuntos externos al código. No afirmar que el sistema está aprobado o validado por INACAP sin evidencia fechada.

### #136 - Humanización

> DRAFT: Humanización implementada parcialmente a nivel de UX y estados, condicionada por WhatsApp y por el pipeline local. No hay streaming audible, barge-in o VAD en WhatsApp. El tono sintético no debe presentarse como atención humana.

### #135 - Nexor

> DRAFT: Investigación comparativa histórica, no evidencia de capacidades de producción. AgroVoz conserva restricciones de privacidad, fallback local y fail-closed agronómico. No afirmar equivalencia funcional con Nexor.

### #50 - Wenuke

> DRAFT: Ideas comparativas, no features automáticamente implementadas. Solo declarar como implementadas las capacidades verificadas en el repositorio actual; integraciones, disponibilidad y cifras de terceros requieren fuente vigente.

### #48 - Valor de venta

> DRAFT: El cálculo está implementado y testeado localmente. La existencia del tool no prueba entrega por WhatsApp ni uso por productores. #214 sigue pendiente para validar el canal real.

### #47 - Resiliencia ODEPA

> DRAFT: El sistema tiene fallbacks y respuestas honestas ante indisponibilidad según tests locales. No equivale a un SLO productivo ni a monitoreo 24/7; esos claims requieren operación observada.

### #36 - Competencia y B2G

> DRAFT: Investigación estratégica. Competidores, cifras de mercado y propuesta B2G deben conservar fecha y fuente. El modelo B2G es hipótesis de negocio, no contrato, ingreso ni validación comercial.

### #35 - Agrivoz/Aioras

> DRAFT: Benchmark histórico de producto externo. No presenta evidencia de capacidades propias ni autorización para copiar claims. Mantener como investigación fechada.

### #34 - Agrovoz.app

> DRAFT: Benchmark histórico de producto externo. Separar features observadas de features implementadas en AgroVoz; no afirmar equivalencia ni disponibilidad de terceros sin verificación actual.

## Registro de publicación

No se incluyen secretos, teléfonos, tokens, transcripciones ni datos del
usuario. La publicación remota se registra en los enlaces de cada Discussion y
no cambia automáticamente la categoría ni marca como respondida una Discussion
sin una respuesta final explícita.
