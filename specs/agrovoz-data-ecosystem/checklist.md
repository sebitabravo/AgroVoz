# Checklist — agrovoz-data-ecosystem

## Phase 1: Explore → Propose

- [x] CHK001 Problema definido: fragmentación, frescura y trazabilidad de información agrícola.
- [x] CHK002 Scope In/Out documentado.
- [x] CHK003 Alternativas consideradas: dataset gigante, marketplace y scraping indiscriminado.
- [x] CHK004 Constitution Check inicial sin violaciones.
- [x] CHK005 Áreas afectadas mapeadas.

## Phase 2: Spec (Requirements)

- [x] CHK006 Cada historia tiene prioridad y justificación.
- [x] CHK007 Cada historia tiene test independiente.
- [x] CHK008 Escenarios Given/When/Then.
- [x] CHK009 FR trazables a tareas/tests.
- [x] CHK010 Edge cases de vacío, vencimiento, error y concurrencia.
- [x] CHK011 Success criteria medibles.
- [x] CHK012 Assumptions explícitos.
- [x] CHK013 Sin NEEDS CLARIFICATION abierto.

## Phase 3: Design

- [x] CHK014 Contexto técnico real del repo.
- [x] CHK015 Constitution Check revalidado.
- [x] CHK016 Modelo `data_sources`/`data_facts` definido.
- [x] CHK017 Paths concretos.
- [x] CHK018 Dependencias existentes; no se agregan paquetes.
- [x] CHK019 Riesgos y mitigaciones.
- [x] CHK020 No hay violaciones que justificar.

## Phase 4: Tasks

- [x] CHK021 Fases Setup/Foundational/US/Polish.
- [x] CHK022 Tareas paralelizables marcadas.
- [x] CHK023 Tareas de implementación etiquetadas por historia.
- [x] CHK024 Test independiente por historia.
- [x] CHK025 Checkpoints entre fases.
- [x] CHK026 Orden de dependencias documentado.

## Phase 5: Apply

- [x] CHK027 Tests focalizados y regresiones en verde; refactor mínimo del prompt y RAG fail-closed verificado.
- [x] CHK028 Cada FR cubierto por al menos un test o gate nativo documentado.
- [x] CHK029 Sin TODOs, secretos, PII ni debug en el diff del Data Hub.
- [ ] CHK030 Conventional Commit solo si el usuario pide commit; este turno no publica.

## Phase 6: Verify

- [x] CHK031 `make test`.
- [x] CHK032 `make lint`.
- [x] CHK033 `make typecheck`.
- [x] CHK034 Cobertura no menor que el gate nativo.
- [x] CHK035 Cada historia funciona independientemente.
- [x] CHK036 Success criteria cumplidos para el alcance local; producción/adaptadores externos siguen siendo gates separados.
- [x] CHK037 Revisión de seguridad del diff.
- [x] CHK038 Rendimiento protegido por guard de prompt, fast path local y ausencia de red en búsqueda/sync de startup.

## Phase 7: Archive

- [x] CHK039 `apply-progress.md` final.
- [x] CHK040 La spec se mantiene en el repositorio; no se archiva hasta una decisión posterior.
- [x] CHK041 Lecciones aprendidas documentadas en el cierre de sesión y memoria persistente.

## Notes

- CHK030 queda pendiente porque no se solicitó commit; la calidad del cambio se verificará sin crear historia remota.
- CHK038 se verifica localmente; no equivale a smoke de producción ni a validar adaptadores externos.
