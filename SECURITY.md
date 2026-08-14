# Política de Seguridad — AgroVoz

> Última actualización: 2026-08-14
> Proyecto INACAP Temuco. El Desafío Crea 2026 y el piloto de Traiguén que dependía de su
> respaldo institucional ya no están vigentes.

## Alcance

AgroVoz es un producto funcional (pipeline E2E de voz y texto, catálogo ODEPA completo, alertas
proactivas, dashboard admin, landing) sin infraestructura propia desplegada 24/7 ni piloto real
con productores en curso. El único deploy público vigente es la demo web gratuita en Vercel
(subconjunto slim: sin WhatsApp, sin modelos locales).

**El modelo de riesgo depende del entorno.** Hoy no hay datos personales de terceros reales en
producción — la demo pública no persiste consultas. Si en el futuro se retoma un piloto con
productores reales, las obligaciones de la Ley 21.719 dejan de ser teóricas y esta política debe
revisarse antes de ese arranque, no después.

| Componente | Soporte de seguridad |
| --- | --- |
| Backend en producción | ✅ Parches y actualizaciones |
| Landing y dashboard admin | ✅ Parches y actualizaciones |

## Cómo reportar una vulnerabilidad

### Para el equipo (interno)

Si encontrás una vulnerabilidad en el código, dependencias o infraestructura:

1. **NO abras un issue público.** El repo es público desde el 14/08/2026: usar GitHub Private
   Vulnerability Reporting (pestaña Security → Report a vulnerability) o el correo de abajo.
2. Abrí un issue con label `security` y `prio:must`.
3. Incluí:
   - Descripción del problema
   - Pasos para reproducir
   - Impacto potencial (datos expuestos, servicio caído, etc.)
   - Sugerencia de fix si tenés una
4. El tech lead (@sebitabravo) revisa en máximo 48 horas.
5. Una vez corregido, el fix se despliega en la siguiente ventana de deploy.

### Para terceros

Usar [GitHub Private Vulnerability Reporting](https://github.com/sebitabravo/AgroVoz/security/advisories/new)
(habilitado desde el 14/08/2026). Alternativa: `sebastian.bravo77@inacapmail.cl`.

## Modelo de seguridad

### Datos que maneja AgroVoz

| Dato                        | Nivel de sensibilidad | Protección                                      |
| --------------------------- | --------------------- | ----------------------------------------------- |
| Audio de WhatsApp (.ogg)    | Alto (voz del usuario) | Almacenado <24h, nombre UUID, borrado automático |
| Transcripción de texto      | Medio                  | Seudonimizada (sin número). Se retiene solo con opt-in explícito |
| Texto de consulta escrita   | Medio                  | Mismo tratamiento que la transcripción           |
| Número de teléfono          | Alto (dato personal)   | HMAC-SHA256 con pepper secreto, irreversible sin el pepper. Nunca en texto claro, logs ni backups |
| Comuna y cultivos declarados | Medio                 | Ligados al hash, no al número                    |
| Historial de consultas      | Medio                  | Tabla `consultation_history`, **solo con opt-in** (`dataset_consent`) |
| Preferencia de alertas      | Bajo                   | `alert_consent`, opt-in separado. Sin él no sale ninguna alerta |
| Consultas de precio/clima   | Bajo                   | Agregadas para métricas, sin PII                 |
| API keys y credenciales     | Crítico                | `.env` excluido de git, vault en VPS             |

> **Nota honesta sobre la seudonimización.** El hash protege ante una filtración de la base de datos,
> pero quien controle el servidor puede asociar consultas al número, porque el mensaje entrante lo
> trae. No es anonimato absoluto y no se presenta como tal.

### Cumplimiento normativo

- **Ley 21.719** (Protección de Datos Personales, Chile, vigente desde diciembre 2026):
  - Números seudonimizados con HMAC-SHA256 y pepper.
  - Audio eliminado del VPS en menos de 24 h.
  - Retención de transcripciones solo con consentimiento explícito.
  - Consentimiento **separado** para alertas proactivas: son comunicación no solicitada y requieren
    su propia base de licitud.
  - Auditoría formal de cumplimiento pendiente antes del escalamiento post-piloto.

  Documentos: `docs/legal/politica-privacidad.md` y `docs/legal/aviso-responsabilidad.md`.
  El acuerdo de consentimiento específico del piloto de Traiguén (`docs/piloto/`) se retiró
  del repositorio: el respaldo institucional de ese piloto (Crea INACAP) ya no existe.

- **Responsabilidad por el dato entregado:** el aviso de responsabilidad se envía en el primer
  contacto por WhatsApp, en los dos canales. Deja explícito que AgroVoz entrega información y no
  recomendaciones, y que los precios de ODEPA son de terminal mayorista y no de predio.

### Principios de seguridad

1. **Zero trust en input externo.** Todo mensaje de WhatsApp se valida (HMAC, rate limit).
2. **Principio de mínimo privilegio.** El worker del pipeline no tiene acceso a la DB de métricas.
3. **Sin recomendaciones agronómicas.** El LLM entrega datos, no interpreta. Esto reduce riesgo de responsabilidad por malas decisiones.
4. **Stack 100% open-source.** Sin APIs pagas externas. Whisper, LLM, TTS y el gateway de WhatsApp
   corren localmente en el VPS. Las consultas no se envían a OpenAI, Google ni Anthropic.
5. **Sin autenticación de usuarios.** Número WhatsApp = identidad. No se almacenan contraseñas.
   Es una decisión de producto (el agricultor no instala ni configura nada), no una omisión: la
   contrapartida es que quien controle el teléfono controla la sesión.
6. **Consentimiento por tratamiento, no global.** Usar el sistema, aportar al dataset de voz y
   recibir alertas son tres permisos distintos y se piden por separado.

### Riesgo declarado: Open-WA

El gateway de WhatsApp es **Open-WA**, un cliente no oficial que opera sobre el protocolo de WhatsApp
Web. Elimina el costo por mensaje, pero implica dos riesgos que conviene tener escritos:

| Riesgo | Consecuencia |
| --- | --- |
| Meta bloquea el cliente o el número | El servicio queda caído hasta migrar a la API oficial |
| Incumplimiento de los términos de servicio de Meta | Puede ser bloqueante para contratar con una institución pública, que revisa el compliance del proveedor |

Resolver esto es requisito previo a cualquier venta institucional. Ver `docs/negocio/07-estructura-de-costos.md`
para el impacto financiero de migrar a la API oficial.

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
| Secret scanning + push protection | ✅ Activo (habilitado 14/08/2026, gratis en repo público) |
| Private Vulnerability Reporting | ✅ Activo (habilitado 14/08/2026) |
| Code scanning (CodeQL)        | ⏳ Workflow preparado (`.github/workflows/codeql.yml`), pendiente de mergear |

## Divulgación responsable

AgroVoz es un proyecto estudiantil sin bug bounty ni programa de recompensas.
Las vulnerabilidades reportadas se corrigen en la siguiente ventana de deploy
del subconjunto público (Vercel). No se divulgan detalles hasta que el fix
esté desplegado.
