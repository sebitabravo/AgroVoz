# Spike técnico: canal IVR de respaldo

**Fecha de evaluación:** 29 de julio de 2026  
**Issue relacionado:** #172  
**Estado:** flujo local validado; conexión PSTN bloqueada por costo y autorización

## Objetivo

Comprobar si AgroVoz puede entregar un precio agrícola mediante una llamada
telefónica, sin depender de una API cerrada ni alterar el canal principal de
WhatsApp.

El spike no habilita telefonía pública en producción. Su alcance termina en un
dialplan local reproducible y en la evaluación documentada del número chileno
entrante.

## Implementación local

El perfil opcional `ivr` de Docker Compose levanta Asterisk sin publicar puertos.
La extensión local `600`:

1. atiende la llamada;
2. espera un segundo;
3. reproduce `agrovoz/precio-papa`;
4. finaliza.

El script `backend/scripts/generate_ivr_prompt.py` consulta el último precio
disponible en ODEPA, genera la locución con Piper y la convierte con ffmpeg a
WAV PCM mono de 8 kHz. Si ODEPA no entrega un precio, el script falla cerrado y
no reemplaza el audio vigente.

Generación del mensaje:

```bash
cd backend
uv run python scripts/generate_ivr_prompt.py --producto papa
```

Prueba local:

```bash
docker compose --profile ivr up -d --build ivr
docker compose --profile ivr exec ivr \
  asterisk -rx "dialplan show agrovoz-local"
docker compose --profile ivr exec ivr \
  asterisk -rx "channel originate Local/600@agrovoz-local application Wait 3"
docker compose --profile ivr rm -sf ivr
```

Evidencia observada:

```text
Executing [600@agrovoz-local:3] Playback(..., "agrovoz/precio-papa")
Playing 'agrovoz/precio-papa.slin'
Executing [600@agrovoz-local:4] Hangup()
```

La prueba valida Asterisk, el dialplan y el formato telefónico. No valida una
llamada desde la red pública, calidad rural, concurrencia ni latencia del
proveedor SIP.

## Evaluación de telefonía pública

- [Asterisk](https://www.asterisk.org/get-started/) es software libre y sirve
  como base de PBX, gateways VoIP e IVR.
- [SUBTEL](https://www.subtel.gob.cl/telefonia-ip/) distingue la telefonía
  privada por Internet de un servicio conectado a la red pública. La
  numeración y la interconexión pública deben ser provistas dentro del marco
  regulado por un concesionario.
- Como referencia verificable al 29 de julio de 2026,
  [Zadarma publicaba](https://zadarma.com/en/tariffs/numbers/chile/mobile/) un
  número móvil chileno por USD 9 de conexión y USD 9 mensuales, impuestos
  incluidos, con desvío a SIP y llamadas entrantes sin cargo adicional.

El software de central no agrega licencia, pero un DID/SIP entrante chileno sí
agrega un costo fijo externo. Para un piloto de 3 a 5 productores, ese costo no
es compatible con la meta de CLP 100–150 por agricultor al mes. Además, comprar
un número, aceptar términos de un proveedor y recibir llamadas públicas exige
autorización del equipo y revisión operativa/legal.

## Decisión

**No desplegar el IVR a PSTN durante el piloto actual.** Se conserva el perfil
local como evidencia técnica y punto de partida. El issue #172 permanece
abierto hasta contar con:

1. presupuesto institucional para DID/SIP;
2. proveedor y condiciones aprobadas;
3. revisión legal/privacidad de metadatos de llamadas;
4. prueba E2E con una llamada chilena real;
5. medición en el hardware mínimo de 1 vCPU y 6 GB RAM.

Una institución PRODESAL/INDAP podría absorber el costo fijo en una fase
posterior. Esa hipótesis requiere validación comercial y no se considera un
compromiso actual.

## Extensión: VAD, streaming y barge-in (C10, 30 de julio de 2026)

Backlog de las Discussions del repositorio: que el productor pueda
**interrumpir la locución hablando encima** (barge-in) en vez de esperar a
que termine, y que la respuesta se transmita en fragmentos cortos en vez de
un único archivo largo. Sigue siendo un spike local: no cambia la decisión
de no desplegar a PSTN.

### Por qué hace falta ARI (y no alcanza con AGI/dialplan)

El flujo original (extensión `600`) usa `Playback()` estático: no hay forma
de saber, desde el dialplan, que el productor empezó a hablar mientras el
audio se reproduce. Asterisk expone esa señal — `TALK_DETECT` y los eventos
`ChannelTalkingStarted`/`ChannelTalkingFinished` — únicamente a los clientes
de **ARI** (REST + WebSocket), no a AGI ni al dialplan puro. Por eso la
extensión nueva `601` entrega el canal a `Stasis(agrovoz-ivr)`.

### Arquitectura

- `backend/app/services/ivr_turn_service.py` — máquina de estados de turno
  (`IvrTurnController`): decide qué acción corresponde ante cada evento
  (cortar la locución, grabar, transcribir, reproducir la respuesta) sin
  conocer Asterisk. 100% de cobertura, más un escenario BDD
  (`tests/features/ivr_barge_in.feature`) para el caso de negocio central:
  el productor interrumpe hablando encima.
- `backend/scripts/ivr_ari_bridge.py` — puente ARI: traduce eventos reales
  de Asterisk a los métodos de `IvrTurnController` y las acciones de vuelta
  a comandos REST (`Playback`, `Record`, `TALK_DETECT`, `Hangup`). Reutiliza
  `TTSService` (fragmentos cortos, igual que el pipeline de WhatsApp),
  `WhisperService` y el fallback determinista por keywords
  (`_force_keyword_tool`) — una sola fuente de verdad para "qué producto
  preguntó", sin duplicar esa lógica para el canal telefónico.
- `ivr/http.conf` + `ivr/ari.conf.template` + `ivr/entrypoint.sh` — habilitan
  la interfaz ARI en el contenedor Asterisk. La contraseña (`IVR_ARI_PASSWORD`)
  se sustituye en el arranque vía `envsubst`, nunca queda commiteada en texto
  plano. El puerto 8088 no se publica al host: solo es alcanzable dentro de
  la red interna de Docker Compose, igual que el resto del perfil `ivr`.

### Qué se verificó localmente

```bash
IVR_ARI_PASSWORD=<clave-dev> docker compose --profile ivr up -d --build ivr
IVR_ARI_PASSWORD=<clave-dev> docker compose --profile ivr exec ivr \
  asterisk -rx "http show status"        # confirma ARI escuchando en 8088
IVR_ARI_PASSWORD=<clave-dev> docker compose --profile ivr exec ivr \
  asterisk -rx "dialplan show agrovoz-local"   # confirma 601 -> Stasis(agrovoz-ivr)
```

Con un cliente WebSocket conectado a
`ws://ivr:8088/ari/events?app=agrovoz-ivr&api_key=agrovoz:<clave>` y un
`channel originate Local/601@agrovoz-local application Wait 3`, se observó
la secuencia real de eventos ARI terminando en:

```text
EVENT TYPE: ChannelCreated
EVENT TYPE: ChannelDialplan
EVENT TYPE: Dial
EVENT TYPE: ChannelDialplan
EVENT TYPE: ChannelVarset
EVENT TYPE: StasisStart
```

Esto confirma la cadena completa **dialplan → Stasis → ARI → evento
recibido por el puente**, que es el riesgo de integración principal de esta
extensión.

### Qué NO se verificó (limitación honesta)

- **Detección real de voz (VAD).** `channel originate ... application Wait`
  no inyecta energía de audio real en el canal: no dispara
  `ChannelTalkingStarted`/`Finished` porque no hay nadie "hablando". Verificar
  el barge-in con voz real requiere un softphone SIP o un archivo de audio
  inyectado en el canal — pendiente para cuando haya un caso de prueba con
  llamada real (ver sección de PSTN arriba).
- **Streaming percibido por el oyente.** Los fragmentos se generan y se
  reproducen secuencialmente vía `Playback`, pero Asterisk reproduce cada
  archivo completo antes del siguiente — no hay streaming de audio en
  progreso (chunked transfer) como en un TTS verdaderamente incremental.
  Es "fragmentado", no "streaming" en el sentido estricto.
- **Detección de producto en la transcripción.** Reutiliza el fallback por
  keywords del canal de WhatsApp; no prueba una consulta compuesta o
  ambigua específica del canal de voz telefónica.

### Estado

Igual que el resto del spike IVR: **no se despliega a producción.** El
issue #172 sigue abierto con las mismas condiciones de la sección
"Decisión" de arriba, más la verificación de VAD con audio real como
requisito adicional antes de considerar esta extensión lista para un
piloto telefónico.
