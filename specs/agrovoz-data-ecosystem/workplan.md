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

- [x] Comparar worktree, `HEAD`, `origin/main`, rama, CI y despliegue.
- [x] Construir matriz requisito → evidencia para los cinco bugs de la ronda 3.
- [x] Construir matriz requisito → evidencia para Data Hub, migración, fuentes,
      secrets, E2E, smoke y rendimiento.
- [x] Identificar cambios previos sin commit y evitar perderlos.
- [x] Confirmar qué credenciales/configuración existen sin imprimir secretos.

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

- [x] Verificar en la fuente oficial vigente cada URL y modalidad declarada.
- [x] Implementar adaptador solo cuando exista endpoint/archivo/contrato estable.
- [x] Integrar Pulso Agroclimático INIA si existe descarga reproducible; la
      verificación concluyó que no hay contrato machine-readable estable, por
      lo que queda `not_connected` con su límite documentado.
- [x] Integrar CIREN/IDE Minagri mediante un servicio interoperable verificable,
      sin ingerir todas sus capas GIS.
- [x] Integrar el catálogo oficial del INE Censo Agropecuario sin descargar sus
      bases masivas automáticamente.
- [x] Integrar CampoClick solo si la institución expone acceso autorizado; no
      se encontró contrato público autorizado y permanece `not_connected`.
- [x] Mantener `not_connected` con evidencia técnica si una fuente no permite
      integración segura; nunca inventar datos ni hacer scraping opaco.
- [x] Testear error, timeout, vigencia y ausencia de datos en los adaptadores
      remotos; conservar licencia/uso como límite explícito por fuente.

**Salida:** catálogo honesto: conectado/healthy, conectado/stale o
`not_connected` con motivo verificable.

### Fase 3 — Operación y configuración productiva

- [x] Revisar `.env.example`, Compose, Dockerfile, healthchecks y volúmenes.
- [x] Confirmar variables obligatorias sin mostrar valores.
- [ ] Configurar `PHONE_HASH_PEPPER`, `OPENWA_API_KEY`, `ADMIN_API_KEY` y
      cualquier secreto requerido mediante el canal existente, no en git.
- [x] Aplicar `alembic upgrade head` en una base temporal y comprobar el head
      único `a7b8c9d0e1f2`; el backup/rollback de la base productiva sigue
      siendo un gate externo.
- [ ] Ejecutar sync ODEPA real y confirmar estado `healthy`/frescura.
- [x] Ejecutar sync local Data Hub y verificar 10 fuentes/121 hechos; el sync
      remoto queda opt-in y autenticado.
- [x] Revisar permisos, logs, media TTL, rate limits y headers en el código y
      Compose; falta confirmación runtime productiva.

**Salida:** entorno operativo configurado y reversible.

### Fase 4 — E2E local

- [x] Levantar backend/landing con configuración de prueba.
- [x] Ejecutar Playwright o runner nativo contra la UI real, no una captura.
- [x] Probar precio real, clima real, consulta de semillas, comuna no soportada,
      seguimiento conversacional, fallback, error de red y TTS.
- [x] Probar catálogo público y endpoint admin sin exponer secretos.
- [x] Medir latencia de cada consulta de la demo; el flujo Whisper real queda
      pendiente de un artefacto de audio controlado en este entorno.
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

- **Fase activa:** Fase 3 — operación y configuración productiva.
- **Fases locales:** Fases 0, 1, 2 y 4 verificadas en esta ejecución; el fix
  E2E de ubicación quedó protegido por regresión automatizada.
- **Fases externas:** secrets productivos, migración/sync productiva, publicación,
  E2E publicado y medición del VPS aún no tienen evidencia en esta ejecución.
- **Última actualización:** 2026-08-13.
