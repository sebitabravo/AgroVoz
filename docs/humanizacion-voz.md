# Humanización de voz en el canal WhatsApp

> Decisión técnica derivada de la
> [Discussion #136](https://github.com/sebitabravo/AgroVoz/discussions/136).
> Estado al 30/07/2026. No reemplaza la validación con productores.

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
| `faster-whisper` INT8 | Implementado localmente | Backend intercambiable en `whisper_service.py` (`WHISPER_BACKEND=faster` por defecto, degrada a `openai` si la librería falta). No instalable en este entorno de desarrollo: falta medir WER rural, latencia y RAM en el piso mínimo de 1 vCPU/4 GB (#215) antes de darlo por confirmado; el VPS de referencia de 8 vCPU/16 GB no reemplaza ese benchmark |
| Silero VAD para fin de habla | No aplicable al WhatsApp actual | El webhook recibe el audio terminado; evaluar solo para recorte de silencios con una hipótesis y benchmark propios |
| Streaming LLM → TTS por oración | Parcialmente implementado (solo el cómputo interno) | El streaming audible sigue sin aplicar (WhatsApp recibe un archivo terminado). Lo que sí se implementó: los fragmentos ya generados se sintetizan en paralelo entre sí (`tts_service.py`, medido 1.32x). Streaming genuino LLM→TTS (empezar a sintetizar mientras el modelo aún genera) se evaluó y se descartó: el LLM aislado (#213) emite tool calls como texto embebido (`<tool_call>...</tool_call>`), y no hay forma de saber si una oración es prosa hablable o parte de una llamada a herramienta hasta que la iteración completa termina. Sintetizar y enviar audio de un fragmento que resulta ser JSON de una tool sería un riesgo real |
| Kokoro en vez de Piper | Rechazado para este ciclo | Licencia compatible (pesos Apache-2.0, wrapper `kokoro-onnx` MIT) y buen soporte de español (3 voces), pero: (1) modelo ~327 MB frente a los ~63 MB de Piper actual, un salto real de RAM en el piso mínimo de 4 GB; (2) requiere `espeak-ng` como dependencia de sistema nueva en la imagen Docker; (3) sin voz específica chilena — la comunidad reporta acento "latino genérico" con las voces `ef_`/`em_`, la misma limitación que ya tiene `es_MX-claude-high`; (4) la pregunta real de #136 ("¿suena más humano?") es un juicio perceptual que ningún benchmark de latencia o tamaño de modelo contesta — requiere que alguien lo escuche. Piper sigue siendo la decisión vigente. Reconsiderar solo con una prueba de audio A/B real con productores, no antes |
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
2. benchmark reproducible pendiente en el piso mínimo de 1 vCPU/4 GB (#215),
   incluida la latencia real de `faster-whisper` y de la síntesis TTS en
   paralelo (medidas solo en hardware de desarrollo hasta ahora, no en el
   piso ni en el VPS de referencia de 8 vCPU/16 GB, que es solo un escenario
   de planificación);
3. cinco conversaciones guiadas y evaluación de comprensión/naturalidad;
4. WER con voces rurales reales y consentimiento válido, comparando
   `faster-whisper` contra `openai-whisper` antes de retirar el respaldo;
5. si se retoma Kokoro, una prueba de audio A/B real con productores — no
   una comparación de benchmarks internos.

