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
