# Modelo de visión local

Artefactos descargados desde `imaflower/plantvillage-mobilenetv3` en Hugging Face.

- Repositorio: https://huggingface.co/imaflower/plantvillage-mobilenetv3
- Licencia declarada por el repositorio: MIT
- Modelo: `plant_disease_mobilenetv3.onnx` + `model.onnx.data`
- Etiquetas: `plant_disease_labels.json`
- Entrada validada con ONNX Runtime: NCHW `float32`, `3x224x224`
- Salida validada: `15` logits
- Advertencia: el model card identifica la arquitectura como ConCaPlant; no se presenta como MobileNetV3 verificado.

Los artefactos de visión se versionan para que el endpoint local y Docker sean reproducibles. Verificar sus hashes antes de usarlos:

```text
c6a962f699ddc820108231b23454b6a4e242407044c2c2514a0f6294d35428fc  plant_disease_mobilenetv3.onnx
c03d114add89db13d850e5bbe0a7c09040cf6caf1acfbbd489c21234fa6dcb5e  model.onnx.data
def913a8dfb724412421d4ca18b74f430d3036f94ffd6605b9640036e30e8735  plant_disease_labels.json
```

La clasificación no autoriza diagnósticos libres. AgroVoz solo cita una regla INIA cuando existe una combinación verificada; para el resto responde sin recomendación.
