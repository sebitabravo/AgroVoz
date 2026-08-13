# Plan maestro — Ecosistema AgroVoz 100%

## Objetivo

Cerrar no solo la implementación local, sino también los gates operativos que
quedaron explícitos en la auditoría: migración, sincronización real, E2E de la
landing/demo, publicación, smoke productivo, configuración segura y medición
del VPS mínimo. No se considera una fase cerrada por intención: cada casilla
requiere evidencia en archivos, logs, tests o runtime.

## Regla de cierre

Una fase se marca completa solo cuando:

1. el cambio está implementado en el alcance correcto;
2. tiene regresión o contrato automatizado cuando corresponde;
3. el comando o runtime que prueba el requisito pasó;
4. los límites externos quedan explícitos y no se maquillan como capacidades.

## Fases

### Fase 0 — Auditoría de estado y requisitos

- [ ] Comparar worktree, `HEAD`, `origin/main`, rama, CI y despliegue.
- [ ] Construir matriz requisito → evidencia para los cinco bugs de la ronda 3.
- [ ] Construir matriz requisito → evidencia para Data Hub, migración, fuentes,
      secrets, E2E, smoke y rendimiento.
- [ ] Identificar cambios previos sin commit y evitar perderlos.
- [ ] Confirmar qué credenciales/configuración existen sin imprimir secretos.

**Salida:** inventario actual, riesgos y orden de ejecución.

### Fase 1 — Corrección local completa

- [x] Eliminar precios/clima simulados y fallback que confunde productos.
- [x] Responder fail-closed para semillas y datos ausentes.
- [x] Validar comunas y eliminar caída silenciosa a Traiguén.
- [x] Conservar contexto conversacional acotado.
- [x] Hacer fallback determinista y proteger el presupuesto del prompt.
- [x] Mostrar/reintentar errores de red en la UI.
- [x] Data Hub con manifest, `data_sources`, `data_facts`, hashes y frescura.
- [x] RAG con procedencia y exclusión de snapshots vencidos.
- [x] APIs públicas/admin y fast path de capacidades.
- [x] Migración reversible y tests de regresión.

**Evidencia existente:** suite local, lint, mypy, build y migración temporal.

### Fase 2 — Fuentes oficiales y cobertura real

- [ ] Verificar en la fuente oficial vigente cada URL y modalidad declarada.
- [ ] Implementar adaptador solo cuando exista endpoint/archivo/contrato estable.
- [ ] Integrar Pulso Agroclimático INIA si existe descarga reproducible.
- [ ] Integrar CIREN/IDE Minagri si existe servicio interoperable verificable.
- [ ] Integrar INE Censo Agropecuario si existe dataset descargable y licencia.
- [ ] Integrar CampoClick solo si la institución expone acceso autorizado.
- [ ] Mantener `not_connected` con evidencia técnica si una fuente no permite
      integración segura; nunca inventar datos ni hacer scraping opaco.
- [ ] Testear error, timeout, licencia, vigencia y ausencia de datos por fuente.

**Salida:** catálogo honesto: conectado/healthy, conectado/stale o
`not_connected` con motivo verificable.

### Fase 3 — Operación y configuración productiva

- [ ] Revisar `.env.example`, Compose, Dockerfile, healthchecks y volúmenes.
- [ ] Confirmar variables obligatorias sin mostrar valores.
- [ ] Configurar `PHONE_HASH_PEPPER`, `OPENWA_API_KEY`, `ADMIN_API_KEY` y
      cualquier secreto requerido mediante el canal existente, no en git.
- [ ] Aplicar `alembic upgrade head` con backup/rollback comprobable.
- [ ] Ejecutar sync ODEPA real y confirmar estado `healthy`/frescura.
- [ ] Ejecutar sync local Data Hub y verificar 9 fuentes/los conteos esperados.
- [ ] Revisar permisos, logs, media TTL, rate limits y headers.

**Salida:** entorno operativo configurado y reversible.

### Fase 4 — E2E local

- [ ] Levantar backend/landing con configuración de prueba.
- [ ] Ejecutar Playwright o runner nativo contra la UI real, no una captura.
- [ ] Probar precio real, clima real, consulta de semillas, comuna no soportada,
      seguimiento conversacional, fallback, error de red y TTS.
- [ ] Probar catálogo público y endpoint admin sin exponer secretos.
- [ ] Medir latencia de cada consulta y del flujo de voz.
- [ ] Corregir cualquier regresión y repetir desde Fase 1.

**Salida:** reporte E2E reproducible con requests, respuestas, tiempos y
errores de consola.

### Fase 5 — Publicación segura

- [ ] Revisar diff completo, secretos, archivos generados y migraciones.
- [ ] Resolver divergencia con `origin/main` sin perder cambios ni reescribir
      historia compartida.
- [ ] Crear commit(s) Conventional Commit enfocados.
- [ ] Ejecutar CI remoto y esperar checks requeridos.
- [ ] Publicar mediante el flujo configurado (Dokploy/GitHub) si la conexión
      está disponible.
- [ ] Conservar un punto de rollback identificable.

**Salida:** artefacto publicado con commit y checks verificables.

### Fase 6 — Smoke productivo y correcciones

- [ ] Confirmar health/readiness públicos.
- [ ] Confirmar migración aplicada y único head.
- [ ] Confirmar sync ODEPA y Data Hub desde el entorno real.
- [ ] Ejecutar 25 consultas de regresión en la landing publicada.
- [ ] Verificar TTS, latencia <15 s, consola y respuestas HTTP.
- [ ] Corregir cualquier fallo, publicar commit nuevo y repetir el smoke.

**Salida:** reporte externo PASS, no solo tests unitarios.

### Fase 7 — Piso de hardware, seguridad y observabilidad

- [ ] Medir en 1 vCPU/4 GB: arranque, memoria, inferencia, sync, RAG y E2E.
- [ ] Confirmar que no hay sobresuscripción ni dependencia externa nueva.
- [ ] Ejecutar revisión de seguridad del diff y de rutas públicas/admin.
- [ ] Verificar backups, rollback de migración, logs sin PII y TTL de medios.
- [ ] Revisar accesibilidad móvil de landing/demo.
- [ ] Registrar cualquier limitación que requiera una decisión explícita.

**Salida:** matriz de calidad local/producción/hardware con límites separados.

### Fase 8 — Auditoría final y cierre

- [ ] Revisar cada requisito de este plan contra evidencia actual.
- [ ] Actualizar `README`, `docs/ARCHITECTURE` y el estado de fases.
- [ ] Registrar commits, URLs, comandos, tiempos y advertencias.
- [ ] Confirmar que no quedan tareas locales ni correcciones conocidas.
- [ ] Marcar el objetivo completo solo si todos los gates exigidos tienen
      evidencia; de lo contrario, mantenerlo activo y describir el bloqueo.

## Estado actual

- **Fase activa:** Fase 0 — auditoría de estado y requisitos.
- **Fases locales:** la implementación de Fase 1 existe en el worktree y debe
  volver a verificarse contra el estado actual.
- **Fases externas:** migración/sync productiva, E2E publicado, publicación y
  medición del VPS aún no tienen evidencia en esta ejecución.
- **Última actualización:** 2026-08-13.
