# Contrato del modelo de visión de WhatsApp

La visión se activa únicamente con `VISION_ENABLED=true`. El modelo y las
etiquetas son artefactos locales provisionados fuera de Git porque el repositorio
no contiene binarios ni un dataset de entrenamiento. Antes de activar el gate,
el equipo debe verificar la licencia, procedencia y checksum del artefacto que
instale en el VPS.

## Configuración

| Variable | Default | Contrato |
|---|---|---|
| `VISION_ENABLED` | `false` | Gate de mensajes `type=image`; apagado por defecto |
| `VISION_MODEL_PATH` | `models/vision/plant_disease_mobilenetv3.onnx` | Archivo ONNX local |
| `VISION_LABELS_PATH` | `models/vision/plant_disease_labels.json` | JSON opcional con lista ordenada de etiquetas |
| `VISION_CONFIDENCE_THRESHOLD` | `0.8` | Umbral inclusivo para consultar la regla agronómica |
| `VISION_IMAGE_MAX_BYTES` | `10485760` | Límite defensivo de media recibida |

## Entrada y salida ONNX

- Entrada: imagen RGB decodificable, redimensionada a `224x224`, `float32`,
  normalizada con media ImageNet `(0.485, 0.456, 0.406)` y desviación
  `(0.229, 0.224, 0.225)`. El tensor NCHW esperado es `[1, 3, 224, 224]`;
  también se acepta NHWC si la sesión lo declara.
- Salida: primer tensor con `N` logits o probabilidades. Se calcula softmax
  cuando no es una distribución válida y se conservan las tres clases de mayor
  confianza.
- Etiquetas: el índice `i` de la salida corresponde a `labels[i]`. Para que la
  regla citada pueda resolver cultivo y enfermedad, se recomienda el formato
  `Cultivo___enfermedad`; sin etiquetas el servicio usa `clase_i` y no inventa
  un nombre.

Si la imagen no alcanza el umbral, el servicio responde que no pudo identificar
con certeza y no consulta reglas. Con confianza suficiente consulta el motor de
reglas vigente; si no hay una regla citada, se conserva su respuesta segura.
Una imagen descargada desde Open-WA solo vive en memoria durante la inferencia y
no se escribe en SQLite ni en disco.
