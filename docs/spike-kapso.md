# Spike: Evaluación de Kapso como alternativa a Open-WA

> Issue #194 — Discussion #135. **Investigación, no implementación.**

## Contexto

Open-WA (gateway self-hosted actual) mantiene una instancia de WhatsApp Web
24/7 en el VPS. Si se desconecta, el servicio muere. El QR scan inicial es
frágil. Este spike evalúa alternativas de API WhatsApp que no requieran
instancia de browser.

## Alternativas evaluadas

### 1. Kapso (https://kapso.io)

| Aspecto | Evaluación |
|---------|-----------|
| Tipo | API WhatsApp Cloud (no requiere browser) |
| Webhooks | Estables, entrega garantizada |
| Audio | Soporta envío/recepción de audio .ogg |
| Costo | ~USD 30-50/mes por número |
| Documentación | Buena, SDK Python disponible |
| Compatibilidad | Reemplazo directo de Open-WA en pipeline |

### 2. WaliChat (https://walichat.com)

| Aspecto | Evaluación |
|---------|-----------|
| Tipo | API WhatsApp Business |
| Costo | ~USD 25-40/mes |
| Audio | Soporte limitado (solo envío, no recepción nativa de audio) |
| SDK | REST API, sin SDK Python oficial |
| Compatibilidad | Requiere adaptación del webhook de recepción de audio |

### 3. Wassenger (https://wassenger.com)

| Aspecto | Evaluación |
|---------|-----------|
| Tipo | API WhatsApp + multidispositivo |
| Costo | ~USD 20-35/mes |
| Audio | Soporta audio .ogg nativo |
| SDK | Node.js principalmente, REST API usable desde Python |
| Compatibilidad | Migración media: webhook + envío diferentes a Open-WA |

## Esfuerzo de migración estimado

| Componente | Esfuerzo | Riesgo |
|-----------|----------|--------|
| Webhook de recepción | 2-4h | Medio: validar formato de payload |
| Envío de audio | 1-2h | Bajo: POST multipart estándar |
| Config Docker Compose | 1h | Bajo: variable de entorno + API URL |
| Migración de datos | 0h | No hay datos de sesión que migrar |
| Testing E2E | 3-4h | Medio: requiere número WhatsApp real |
| **Total estimado** | **7-11h** | |

## Tensión con hard constraint

El hard constraint del proyecto es "stack 100% open-source, sin APIs pagas
externas". **Kapso/WaliChat/Wassenger rompen esta restricción** al requerir
un costo mensual (USD 20-50/mes). Open-WA es gratuito y self-hosted.

## Recomendación

**Seguir con Open-WA.** Solo reconsiderar si:

1. El piloto Traiguén (3-5 productores, 4 semanas) muestra caídas
   frecuentes de Open-WA (>2 caídas/semana que requieran intervención manual).
2. Hay presupuesto institucional (INDAP/PRODESAL, B2G) que absorba el costo
   mensual de una API paga.
3. El costo por agricultor se mantiene bajo CLP 150/mes (constraint del
   proyecto).

Si se cumplen las 3 condiciones, **Kapso es la mejor alternativa** por
soporte nativo de audio y SDK Python.

## Decisión

Pendiente de registrar en `docs/ARCHITECTURE.md` sección Decisiones.
Por ahora: seguir con Open-WA. Solo reconsiderar si el piloto muestra
inestabilidad y hay presupuesto institucional.
