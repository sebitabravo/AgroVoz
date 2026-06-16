# Skill: agrovoz-testing-coverage

## Propósito
Cobertura de testing consistente. Cada feature y bug fix llega con tests.

## Cuándo usarlo
Al escribir feature, arreglar bug o revisar PR.

## Stack
- pytest + pytest-asyncio + pytest-cov
- httpx `AsyncClient` para tests de API (ASGI transport)
- Fixtures en `conftest.py`

## Regla de oro

- **Cada feature nueva requiere tests.** Sin excepciones.
- **Cada bug fix requiere test de regresión** que FALLA sin el fix.
- Tests **deterministas**: sin `random`, sin tiempo real, sin red real.
- Tests **rápidos**: si uno toma >2s, mockear la dependencia lenta.

## Estructura

- Un archivo de test por módulo/componente: `test_<modulo>.py`.
- `describe` anida escenarios. `it`/`def test_` describe comportamiento esperado.
- Nombres describen comportamiento, no implementación.

```python
# BIEN
def test_get_precio_devuelve_precio_cuando_existe(): ...

# MAL
def test_get_precio(): ...
```

## Qué testear

1. **Black box** lógica de negocio: inputs/outputs esperados.
2. **Edge cases**: vacío, null, límites, caracteres especiales, audio vacío.
3. **Errores**: qué pasa cuando falla (no solo happy path).
4. **Contratos API**: status codes, schema de response, headers.

## Qué NO testear

- Implementación interna (métodos privados que no afectan output).
- Código del framework (routing básico, serialización del ORM).
- Tests de mocks/fixtures (no testear los helpers de test).

## Patrones AgroVoz

### Test de endpoint (API)

```python
@pytest.mark.asyncio
async def test_get_precio_retorna_200_y_precio(client):
    # arrange
    await seed_precio("papa", mercado="temuco", valor=1200)
    # act
    resp = await client.get("/api/v1/precios/papa?mercado=temuco")
    # assert
    assert resp.status_code == 200
    assert resp.json()["valor"] == 1200
```

### Test con dependencia externa mockeada

```python
@pytest.mark.asyncio
async def test_sync_odepa_maneja_timeout(client, mock_httpx_timeout):
    with pytest.raises(OdepasyncError):
        await sync_odepa()
```

Mockear: httpx (OpenWeatherMap, Open-WA API), Whisper, LLM, Piper, filesystem de audio.

### Test de regresión (bug fix)

```python
def test_whisper_no_confunde_papa_con_para(transcribe):
    # bug: "papa" → "para". Fix: diccionario de corrección rural.
    texto = transcribe("fixtures/audio_papa.wav")
    assert "papa" in texto
    assert "para" not in texto
```

## Ejecutar

```bash
cd backend
uv run pytest tests/ -v                 # todo
uv run pytest tests/ -v --cov=app       # con cobertura
uv run pytest tests/api/ -k precio      # filtrar por nombre
uv run pytest tests/ -x                 # parar al primer fallo
```

## Fixtures base (conftest.py)

```python
@pytest_asyncio.fixture
async def client():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

## Anti-patrones

```python
# MAL — depende del orden de ejecución
precios_cache = []
def test_a(): precios_cache.append(1)
def test_b(): assert len(precios_cache) == 1  # frágil

# BIEN — cada test aísla su estado
@pytest.mark.asyncio
async def test_get_precio(client, db_limpio): ...

# MAL — happy path nomás
def test_procesar_audio(): procesar("ok.wav")  # sin assert de error

# BIEN — cubre error también
def test_procesar_audio_vacio(): ...
def test_procesar_audio_formato_incorrecto(): ...
```
