# Humanización de voz en el canal WhatsApp

> Decisión técnica derivada de la
> [Discussion #136](https://github.com/sebitabravo/AgroVoz/discussions/136).
> Estado al 29/07/2026. No reemplaza la validación con productores.

## Restricción del canal

AgroVoz no participa de una llamada en tiempo real. WhatsApp entrega al webhook
una nota de voz que el productor ya terminó de grabar y la respuesta vuelve como
otro archivo de audio completo. Por lo tanto, el backend no controla el botón de
grabación ni reproduce audio mientras el usuario habla.

Esta diferencia impide trasladar literalmente patrones de agentes telefónicos:

- un VAD dentro del backend no puede decidir cuándo WhatsApp deja de grabar;
- no existe barge-in sobre una nota de voz ya enviada;
- generar TTS por frases no permite escuchar la primera mientras se produce la
  última, porque WhatsApp recibe un archivo terminado;
- enviar cada frase como audio separado cambiaría el producto, multiplicaría
  mensajes y necesitaría probar orden, entrega y experiencia en terreno.

VAD, streaming audible y barge-in solo vuelven a tener sentido si el proyecto
activa un canal síncrono, como el IVR descrito en `spike-ivr.md`.

## Decisión por propuesta

| Propuesta de #136 | Decisión | Evidencia o gate |
|---|---|---|
| Frases breves, una pregunta por turno, transparencia y cierre suave | Implementada localmente | Reglas en `prompt_builder.py` y regresiones de prompt |
| Tres caminos distintos ante audio no comprendido | Implementada localmente | Contador efímero en la máquina de estados y tests de repetición, texto y derivación informativa |
| Señal inmediata de procesamiento | Implementada como presencia de WhatsApp | `AudioService` activa `recording` o `typing` antes del pipeline y la limpia al terminar; falta validarla con Open-WA real en #214 |
| Audio “déjeme buscar” separado | No adoptado para el piloto | Agrega un segundo envío, costo de red y riesgo de orden; reconsiderar solo con evidencia de que el indicador no es visible o suficiente |
| Worker reiniciable de `llama-cpp` | Implementado localmente | Proceso `spawn`, timeout destructivo, reinicio, circuit breaker y tests de crash/bloqueo |
| `faster-whisper` INT8 | Experimento pendiente | Requiere comparar WER rural, latencia, RAM y tiempo de carga en 1 vCPU/6 GB; no se agrega una dependencia sin ese benchmark |
| Silero VAD para fin de habla | No aplicable al WhatsApp actual | El webhook recibe el audio terminado; evaluar solo para recorte de silencios con una hipótesis y benchmark propios |
| Streaming LLM → TTS por oración | No aplicable como streaming audible | Podría solapar cómputo interno, pero el usuario no oye nada hasta enviar el archivo completo; evaluar en IVR |
| Kokoro en vez de Piper | Postergado | Piper continúa como decisión vigente; una sustitución exige prueba de español, licencia, CPU, RAM, tamaño de imagen y calidad con productores |
| Barge-in | No aplicable al WhatsApp actual | Requiere audio bidireccional en tiempo real; candidato exclusivo para un IVR futuro |

## Comportamiento conversacional implementado

AgroVoz se identifica como asistente automático y no promete una derivación
humana inexistente. Las respuestas de voz se mantienen cortas, evitan entusiasmo
o empatía fingidos y formulan como máximo una pregunta por turno.

Cuando una consulta no se comprende, la sesión efímera avanza por tres salidas:

1. pedir que repita más despacio y con una sola pregunta;
2. sugerir que escriba el mensaje;
3. transparentar que no hay atención humana dentro del chat y sugerir contacto
   directo con PRODESAL o INDAP.

El contador se reinicia tras una consulta comprendida o al vencer la sesión. No
persiste audio, texto, teléfono ni contenido de la conversación.

## Evidencia pendiente

La implementación local no demuestra naturalidad ni el target de menos de
15 segundos. Antes de cerrar #136 se necesita:

1. prueba autenticada de texto y audio con Open-WA (#214);
2. benchmark completo en 1 vCPU/6 GB (#215);
3. cinco conversaciones guiadas y evaluación de comprensión/naturalidad;
4. WER con voces rurales reales y consentimiento válido;
5. decisión posterior basada en métricas sobre `faster-whisper` y TTS.

