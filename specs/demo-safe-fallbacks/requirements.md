# Requirements — demo-safe-fallbacks

## Meta
- **Feature:** demo-safe-fallbacks
- **Author:** Codex
- **Status:** spec_ready
- **Date:** 2026-08-13
- **Constitution:** [x] Verificado contra `AGENTS.md`

## Functional Requirements

### FR-001 — Fallback sin datos simulados
Given que no hay proveedor LLM disponible, when una consulta no resuelve una fuente determinista, then AgroVoz responde indisponibilidad segura y nunca entrega un valor, producto o fuente simulado.

### FR-002 — Semillas y consultas compuestas
Given que se pide precio de semillas, when aparecen uno o más cultivos, then AgroVoz comunica que no tiene fuente verificable de semillas y nunca reutiliza el precio ODEPA del producto fresco.

### FR-003 — Ubicación explícita segura
Given una comuna explícita no está soportada, when se consulta clima actual o pronóstico, then AgroVoz informa la limitación y no usa Traiguén.

### FR-004 — Contexto demo acotado
Given un segundo turno breve dependiente del anterior, when la demo lo envía, then el backend recibe y valida hasta seis mensajes no persistidos y resuelve referencias climáticas inequívocas con la última ubicación conocida.

### FR-005 — Error visible y reintento
Given que falla `fetch`, when la consulta ya está en chat, then la demo agrega una respuesta visible con control para reintentar esa consulta.

## Non-functional Requirements
- Historial: máximo seis mensajes, roles `user|assistant`, 500 caracteres por mensaje; no se acepta para audio.
- No se exponen excepciones, prompts, herramientas ni secretos.
- Las regresiones no usan red ni modelos reales.

## Out of Scope
- Memoria persistente de conversaciones.
- Precio de semillas sin fuente oficial.
- Cobertura de clima para toda comuna o país.
