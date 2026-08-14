# 6. Modelo de negocio

> Parte del plan de negocio de AgroVoz. Índice en [`docs/negocio/README.md`](./README.md).
> Área PMBOK relacionada: Costos (fuentes de ingreso)
>
> **Actualización 2026-08-14:** el piloto del Crea INACAP mencionado abajo no continuó — el
> proyecto no sigue en ese desafío.

---

## Recursos para la implementación real

Para implementar AgroVoz a escala más allá del piloto del Crea INACAP, los recursos se agrupan en cuatro categorías.

**Financiamiento para desarrollo y operación** (año 1: $25 a $40 millones CLP)

| Recurso | Propósito | Costo mensual estimado |
|---|---|---|
| VPS (multiples instancias — Hetzner CX43 o superior) | Alta disponibilidad, balanceo de carga | EUR 35-70/mes (~CLP 37.000-74.000) |
| WhatsApp Business API oficial vía BSP | Contingencia si Open-WA deja de operar, o si un contrato institucional exige la API oficial (ver riesgo en 8.3) | Por conversación de 24 h, tarifa según categoría. **Pendiente de verificar para Chile** |
| Open-Meteo | Sin costo de plan/API key; cuota documentada de 10.000 requests/día. Requiere cache y rate limiting | CLP 0 |
| Dominio + SSL + monitoreo | Producción | ~CLP 25.000/mes |
| Fine-tuning Whisper | Mejorar precisión con español chileno rural | Tiempo de desarrollo |
| Dedicación 2-3 desarrolladores full-time (8-12 meses) | Desarrollo escalado | $20-30M CLP año 1 |
| Ingeniero agrónomo asesor part-time | Validar pertinencia técnica de recomendaciones | $3-5M CLP año 1 |

**Mentorías y acompañamiento técnico-comercial**: modelos de negocio para sector público y AFC (CORFO, ProChile), acompañamiento legal para acuerdos institucionales con servicios públicos, y mentoría en transferencia tecnológica agrícola vía INIA.

**Alianzas institucionales objetivo (no formalizadas)**:

- Objetivo: convenio marco con INDAP Araucanía y nacional.
- Objetivo: acuerdo de colaboración técnica con INIA Carillanca para validación científica de recomendaciones agrometeorológicas.
- Objetivo: memorando con Subtel en el marco del Plan Nacional de Conectividad Digital Rural y Brecha Digital Cero.
- Objetivo: convenios con municipios rurales de La Araucanía, partiendo por Traiguén.

El estado actual mantiene pendiente la carta de respaldo y el contacto formal; no se presenta ningún
convenio, memorando o alianza como firmado sin un artefacto verificable.

**Acceso a fondos públicos de innovación**: postulación a FIA (Fondo de Innovación Agraria), CORFO Semilla, FONDEF de Aplicación Productiva, y al programa IICA-INDAP 2024-2028 (USD $12,3 millones) explícitamente orientado a modernización digital de la AFC.

## Roadmap de modelo de negocio: suscripción institucional B2G

**Hipótesis de canal — Suscripción institucional vía INDAP/PRODESAL**

La hipótesis es que INDAP o PRODESAL pueda financiar una suscripción institucional anual por
"Agricultor Activo", con un valor referencial de CLP 500-1.000 por agricultor/mes. AgroVoz se
plantea como complemento al extensionismo presencial y el acceso del agricultor sería gratuito si
una institución patrocinadora financia el servicio. El canal, el precio y la contratación todavía
deben validarse mediante el piloto y el proceso institucional correspondiente.

**Nota sobre contratación pública (Mercado Público)**: La venta al Estado chileno —a través de INDAP o cualquier servicio público— requiere navegar el sistema de compras públicas. Contratos sobre 3 UTM (~CLP 200.000) deben licitarse mediante Mercado Público (mercadopublico.cl). AgroVoz necesitaría registrarse como proveedor del Estado (ChileProveedores), participar en licitaciones públicas o convenios marco, y competir contra otros oferentes. El proceso completo —desde el primer contacto institucional hasta un contrato firmado— puede tomar 18-24 meses. Esta complejidad se reconoce como parte del camino de escalamiento, no como barrera insalvable: programas como PROGYSO 2026 e IICA-INDAP abren ventanas de financiamiento específicas para innovación tecnológica en la AFC, que pueden acelerar el procurement mediante convenios de transferencia tecnológica.

**Acceso para agricultores — Gratuito y sin planes de pago**

AgroVoz no vende suscripciones directamente a los agricultores. El productor accede sin cobros, cuotas mensuales ni funciones bloqueadas por un plan comercial; el financiamiento y la cobertura se acuerdan con INDAP, PRODESAL u otra institución patrocinadora.

**Canal complementario — Convenios institucionales y privados**

Cooperativas, asociaciones gremiales y empresas con programas de proveedores AFC podrían financiar
el acceso de los productores a AgroVoz como parte de su cadena de proveedores o de iniciativas de
RSE. Es una vía complementaria de roadmap: no hay convenios privados verificados en este estado.
