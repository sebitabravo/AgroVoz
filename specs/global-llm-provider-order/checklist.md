# Checklist — global-llm-provider-order

## Explore → Propose

- [x] Problema y scope global documentados.
- [x] Se consideraron local-first, remote-first total y remote-first read-only.
- [x] Se eligió remote-first con fallback local y protección de escrituras.
- [x] Se verificó el blast radius con CodeGraph.

## Requirements → Design

- [x] User stories y FR-001..FR-006 trazables.
- [x] Deadline total diferenciado del timeout por request.
- [x] Contexto de conversación preservado.
- [x] Tools mutables y riesgos de duplicación documentados.
- [x] Rollback `LLM_PRIMARY_PROVIDER=local` documentado.

## Apply

- [x] Tests de remoto primero, timeout y escritura local agregados.
- [x] Demo y pipeline usan `answer_with_provider_order()`.
- [x] No se agregaron dependencias ni secretos.
- [ ] Commit: no solicitado.

## Verify

- [x] `make test` final.
- [x] Ruff final.
- [x] Mypy final.
- [x] `git diff --check` final.
- [x] Compose dev/prod `config --quiet` final.
- [ ] Prueba externa real: pendiente de API key/autorización.

## Nota

La prueba externa no se inventará. El cliente local/mock cubre contrato y fallback;
latencia, rate limit y disponibilidad real requieren una key válida.
