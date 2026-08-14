# Constitution — ai-agronomic-guidance

## Rules

1. Una recomendación agronómica solo puede provenir de una regla versionada,
   vigente y citada.
2. Si el corpus vence, falta o no calza, AgroVoz debe decir que no tiene el
   dato; no puede completar con conocimiento general del LLM.
3. La orientación no es diagnóstico personalizado ni reemplaza a un agrónomo.
4. El gate `AGRONOMIC_RULES_ENABLED` debe ser explícito por runtime y el
   default de librería debe permanecer fail-closed.
5. La respuesta conserva fuente, fecha y un límite breve apto para voz.
