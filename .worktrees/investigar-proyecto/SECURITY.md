# Política de Seguridad — AgroVoz

> Última actualización: 2026-06-17
> Repositorio privado. Proyecto estudiantil Desafío Crea INACAP 2026.

## Versiones soportadas

AgroVoz está en etapa MVP implementado — pipeline E2E de voz funcionando, landing page y dashboard admin. El piloto de validación con 3-5 productores en Traiguén está en preparación.

| Versión         | Soporte de seguridad                          |
| --------------- | --------------------------------------------- |
| MVP (actual) | ✅ Parches de seguridad y actualizaciones |
| Pre-MVP (completado) | ✅ Tooling de seguridad configurado |

## Cómo reportar una vulnerabilidad

### Para el equipo (interno)

Si encontrás una vulnerabilidad en el código, dependencias o infraestructura:

1. **NO abras un issue público.** El repo es privado, pero mantenemos el reporte en canal restringido.
2. Abrí un issue con label `security` y `prio:must`.
3. Incluí:
   - Descripción del problema
   - Pasos para reproducir
   - Impacto potencial (datos expuestos, servicio caído, etc.)
   - Sugerencia de fix si tenés una
4. El tech lead (@sebitabravo) revisa en máximo 48 horas.
5. Una vez corregido, el fix se despliega en la siguiente ventana de deploy.

### Para terceros (post-lanzamiento)

Una vez que AgroVoz sea público (post-clasificación Crea INACAP), se habilitará
GitHub Private Vulnerability Reporting. Mientras tanto, contactar a
`sebastian.bravo13@inacapmail.cl`.

## Modelo de seguridad

### Datos que maneja AgroVoz

| Dato                        | Nivel de sensibilidad | Protección                                      |
| --------------------------- | --------------------- | ----------------------------------------------- |
| Audio de WhatsApp (.ogg)    | Alto (voz del usuario) | Almacenado <24h, nombre UUID, borrado automático |
| Transcripción de texto      | Medio                  | Anonimizada (sin phone number), sin historial    |
| Número de teléfono          | Alto (dato personal)   | Hasheado (SHA-256), nunca en logs ni backups     |
| Consultas de precio/clima   | Bajo                   | Agregadas para métricas, sin PII                 |
| API keys y credenciales     | Crítico                | `.env` excluido de git, vault en VPS             |

### Cumplimiento normativo

- **Ley 21.719** (Protección de Datos Personales, Chile, diciembre 2026):
  - Phone numbers hasheados (seudonimización).
  - Audio eliminado del VPS en <24h.
  - Transcripciones anonimizadas antes de almacenar.
  - Auditoría formal de cumplimiento antes del escalamiento post-piloto.

### Principios de seguridad

1. **Zero trust en input externo.** Todo mensaje de WhatsApp se valida (HMAC, rate limit).
2. **Principio de mínimo privilegio.** El worker del pipeline no tiene acceso a la DB de métricas.
3. **Sin recomendaciones agronómicas.** El LLM entrega datos, no interpreta. Esto reduce riesgo de responsabilidad por malas decisiones.
4. **Stack 100% open-source.** Sin APIs pagas externas. Whisper, LLM, TTS y WhatsApp gateway corren localmente en VPS.
5. **Sin autenticación de usuarios en MVP.** Número WhatsApp = identidad. No se almacenan contraseñas.

## Dependencias

### Monitoreo automático

- **Dependabot alerts**: activo (CVE + malware). Notifica vulnerabilidades conocidas en dependencias Python, npm, Docker y GitHub Actions.
- **Dependabot security updates**: activo. PRs automáticos SOLO para parches de seguridad (no nuevas versiones).
- **Dependabot version updates**: configurado con **cooldown obligatorio de 7 días** (14 para major). Los PRs de actualización esperan una semana antes de abrirse, dando tiempo a la comunidad a detectar paquetes maliciosos.

### Política de actualización

| Tipo de actualización | Ventana      | Responsable       |
| --------------------- | ------------ | ----------------- |
| Parche de seguridad   | <48 horas    | Dependabot auto-PR  |
| Versión nueva (minor) | 7 días       | Dependabot (lunes)  |
| Versión nueva (major) | 14 días      | Dependabot (lunes)  |
| Manual                | Según criterio | Tech lead          |

### Agregar nuevas dependencias

- Justificar en el issue/PR por qué es necesaria.
- Verificar que el paquete es legítimo (no typo-squatting).
- Preferir stdlib de Python sobre dependencias externas.
- No se aceptan dependencias de repos git (solo PyPI/npm oficial con lockfile).

## Herramientas de seguridad activas

| Herramienta                   | Estado                                            |
| ----------------------------- | ------------------------------------------------- |
| Dependabot alerts + malware   | ✅ Activo                                          |
| Dependabot security updates   | ✅ Activo                                          |
| Dependabot version updates    | ✅ Configurado (cooldown 7d)                       |
| Claude Code Review            | ✅ GitHub Actions (four-pass: Find → Verify → Assess) |
| CI: ruff lint + mypy strict   | ✅ GitHub Actions (se activa con `pyproject.toml`) |
| CI: pytest + coverage         | ✅ GitHub Actions                                  |
| PR gate: issue-first + labels | ✅ `pr-check.yml`                                  |
| Code scanning (CodeQL)        | ⏳ No disponible en repo privado sin Advanced Security |
| Secret scanning + push prot.  | ⏳ No disponible en repo privado sin Advanced Security |

## Divulgación responsable

AgroVoz es un proyecto estudiantil sin bug bounty ni programa de recompensas.
Una vez público, las vulnerabilidades reportadas se corrigen en la siguiente
ventana semanal de deploy. No se divulgan detalles hasta que el fix esté
desplegado en el VPS de piloto.
