# Skill: agrovoz-python-standards

## Propósito
Calidad de código Python consistente. Evita errores tontos de tipado, async y
seguridad en el backend (FastAPI + SQLAlchemy + Pydantic).

## Cuándo usarlo
Al escribir o revisar código Python en `backend/`.

## Stack fijo
- Python 3.12+, FastAPI 0.115+, SQLAlchemy 2.0+, Pydantic v2, httpx 0.28+
- ruff (lint + format), mypy (strict)
- uv (no pip directo)

## Reglas críticas

- **Type hints en TODAS las funciones** (mypy strict). Sin `Any` salvo justificación.
- **Async por defecto**. Endpoints, services y clientes httpx son `async def`.
- **`const` over `let`**: usar `final` para constantes, preferir inmutabilidad.
- **Funciones cortas** (1 responsabilidad, <30 líneas ideal). Si el nombre necesita "y", dividir.
- **Early returns** sobre `if/else` anidado.
- **Comentarios en español** para el PORQUÉ, no el QUÉ.
- **Docstring en español** para funciones públicas.
- **snake_case** funciones/variables, **PascalCase** clases.
- **<4 parámetros** por función. Más → DTO/objeto.

## Seguridad (no negociable)

- NUNCA `eval()`, `exec()`, `Function()`, `os.system()` con strings dinámicos.
- Subprocess solo con lista de args (no `shell=True`): `subprocess.run(["ffmpeg", ...])`.
- SQL SIEMPRE parametrizado (SQLAlchemy core/ORM). Nunca concatenar queries.
- Input del usuario validado en backend (Pydantic), aunque el frontend valide.
- Output saneado (Jinja2 autoescape ON en admin). Prevención XSS.
- Secrets en `.env` (vault refs), nunca en código ni commiteados.

## Capas (no mezclar)

```
api/ (controllers) → services/ (lógica) → repositories/ (data access)
```

- **api/**: parsear input HTTP, delegar a service, formatear response. CERO lógica de negocio.
- **services/**: lógica pura. Sin conocimiento de HTTP ni DB directa.
- **models/** (SQLAlchemy): esquema. **schemas/** (Pydantic): DTOs request/response.

## Patrones AgroVoz

- **FastAPI router**: un router por recurso en `app/api/`. Deps inyectadas vía `Depends`.
- **Pydantic v2**: `BaseModel` con validators. `model_config = ConfigDict(...)`.
- **SQLAlchemy 2.0**: `Mapped[...]` / `mapped_column`, sesiones async (`AsyncSession`).
- **httpx**: cliente async con timeout explícito. Reusable vía dependencia.
- **Settings**: `pydantic-settings`, `BaseSettings` lee `.env`.

## Validación antes de pushear

```bash
cd backend
uv run ruff check app/           # lint limpio
uv run ruff format app/          # formato
uv run mypy app/                 # type check
uv run pytest tests/ -v          # tests verdes
```

## Anti-patrones

```python
# MAL — sin type hints
def get_price(producto):
    ...

# BIEN
def get_price(producto: str, mercado: str) -> PrecioOut: ...

# MAL — lógica de negocio en el controller
@router.get("/precios/{producto}")
async def precios(producto: str):
    db = sqlite3.connect(...)   # DB en controller
    if producto == "papa":      # lógica de negocio en controller
        ...

# BIEN — controller delega
@router.get("/precios/{producto}", response_model=PrecioOut)
async def precios(producto: str, svc: PrecioService = Depends()) -> PrecioOut:
    return await svc.get(producto)

# MAL — subprocess con shell
subprocess.run(f"ffmpeg -i {archivo}", shell=True)  # inyección

# BIEN — lista de args
subprocess.run(["ffmpeg", "-i", archivo, salida])
```
