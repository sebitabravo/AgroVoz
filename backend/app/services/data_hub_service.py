"""Servicio transversal del Data Hub de AgroVoz.

El servicio valida un manifest de fuentes oficiales, normaliza snapshots YAML
sin PII y mantiene el estado de vigencia en SQLite. No descarga sitios durante
una consulta del agricultor: una integración externa debe tener un adaptador
verificado y su propia sincronización explícita.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.data_hub import DataFact, DataSource

logger = logging.getLogger(__name__)

_DEFAULT_CORPUS_DIR = Path(__file__).parent.parent.parent / "corpus"
_DEFAULT_MANIFEST_PATH = _DEFAULT_CORPUS_DIR / "fuentes_datos.yaml"
_ALLOWED_MODES = frozenset({"live", "snapshot", "database"})
_ALLOWED_STATUSES = frozenset(
    {"healthy", "stale", "error", "disabled", "not_connected", "not_synced"}
)
_ALLOWED_HOSTS = (
    "gob.cl",
    "odepa.gob.cl",
    "indap.gob.cl",
    "inia.cl",
    "agrometeorologia.cl",
    "ciren.cl",
    "ine.gob.cl",
    "open-meteo.com",
)
_ALLOWED_REMOTE_ADAPTERS = frozenset(
    {
        "inia_red_agrometeorologica",
        "ciren_ide_minagri",
        "ine_censo_catalogo",
    }
)


class DataHubValidationError(ValueError):
    """Entrada de manifest o snapshot inválida y no persistible."""


@dataclass(frozen=True, slots=True)
class SourceManifestEntry:
    """Entrada validada del catálogo declarativo."""

    key: str
    name: str
    organization: str
    category: str
    mode: str
    url: str
    license: str
    refresh_policy: str
    coverage: str
    schema_version: int
    corpus_files: tuple[str, ...]
    connected: bool
    remote_adapter: str | None
    verified_on: datetime.date
    review_before: datetime.date


@dataclass(frozen=True, slots=True)
class CorpusFact:
    """Hecho extraído de un snapshot YAML antes de persistirlo."""

    source_key: str
    domain: str
    source_name: str
    subject: str
    location: str | None
    product: str | None
    title: str
    text: str
    source_url: str
    source_date: str | None
    verified_on: datetime.date
    review_before: datetime.date | None


@dataclass(frozen=True, slots=True)
class DataHubSyncResult:
    """Resultado determinista de una sincronización local."""

    sources_synced: int
    facts_synced: int
    stale_sources: tuple[str, ...]
    not_connected_sources: tuple[str, ...]


def _utcnow_naive() -> datetime.datetime:
    """Entrega UTC sin tzinfo para compatibilidad con DateTime de SQLite."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _text(value: object, field: str, *, required: bool = False) -> str:
    """Normaliza texto YAML y valida campos obligatorios."""
    if not isinstance(value, str):
        if required:
            raise DataHubValidationError(f"Campo obligatorio ausente o inválido: {field}")
        return ""
    value = " ".join(value.split())
    if required and not value:
        raise DataHubValidationError(f"Campo obligatorio vacío: {field}")
    return value


def _date(value: object, field: str, *, required: bool = False) -> datetime.date | None:
    """Parsea una fecha ISO sin aceptar precisión inventada."""
    if value is None or value == "":
        if required:
            raise DataHubValidationError(f"Fecha obligatoria ausente: {field}")
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if not isinstance(value, str):
        raise DataHubValidationError(f"Fecha inválida en {field}")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise DataHubValidationError(f"Fecha inválida en {field}: {value}") from exc


def _validate_url(value: object, field: str) -> str:
    """Acepta solo HTTPS y dominios institucionales catalogados."""
    url = _text(value, field, required=True)
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DataHubValidationError(f"URL inválida en {field}") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password or port:
        raise DataHubValidationError(f"URL HTTPS institucional requerida en {field}")
    if not any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in _ALLOWED_HOSTS):
        raise DataHubValidationError(f"Dominio no catalogado en {field}: {hostname}")
    return url


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    """Carga un YAML como mapping y falla cerrado si está corrupto."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DataHubValidationError(f"No se pudo leer {path.name}") from exc
    if not isinstance(data, dict):
        raise DataHubValidationError(f"El snapshot {path.name} no es un mapping YAML")
    return {str(key): value for key, value in data.items()}


def load_manifest(path: str | Path | None = None) -> list[SourceManifestEntry]:
    """Carga y valida todas las fuentes del manifest versionado."""
    manifest_path = Path(path) if path else _DEFAULT_MANIFEST_PATH
    data = _load_yaml_mapping(manifest_path)
    raw_sources = data.get("fuentes")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise DataHubValidationError("El manifest debe contener una lista no vacía de fuentes")

    entries: list[SourceManifestEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise DataHubValidationError(f"Fuente #{index + 1} inválida")
        key = _text(raw.get("clave"), f"fuentes[{index}].clave", required=True)
        if key in seen:
            raise DataHubValidationError(f"Clave duplicada en manifest: {key}")
        seen.add(key)
        mode = _text(raw.get("modo"), f"fuentes[{index}].modo", required=True)
        if mode not in _ALLOWED_MODES:
            raise DataHubValidationError(f"Modo no soportado para {key}: {mode}")
        corpus = raw.get("corpus", [])
        if not isinstance(corpus, list) or not all(isinstance(item, str) for item in corpus):
            raise DataHubValidationError(f"corpus inválido para {key}")
        verified_on = _date(raw.get("verificado_el"), f"{key}.verificado_el", required=True)
        review_before = _date(
            raw.get("revisar_antes_de"), f"{key}.revisar_antes_de", required=True
        )
        assert verified_on is not None
        assert review_before is not None
        if review_before < verified_on:
            raise DataHubValidationError(f"La revisión precede la verificación en {key}")
        connected = raw.get("conectado")
        if not isinstance(connected, bool):
            raise DataHubValidationError(f"conectado debe ser booleano en {key}")
        raw_adapter = raw.get("adaptador")
        if raw_adapter is None:
            remote_adapter = None
        else:
            remote_adapter = _text(raw_adapter, f"{key}.adaptador", required=True)
            if remote_adapter not in _ALLOWED_REMOTE_ADAPTERS:
                raise DataHubValidationError(f"Adaptador remoto no soportado para {key}")
        try:
            schema_version = int(raw.get("version_esquema", 1))
        except (TypeError, ValueError) as exc:
            raise DataHubValidationError(f"version_esquema inválida en {key}") from exc
        if schema_version < 1:
            raise DataHubValidationError(f"version_esquema inválida en {key}")
        entries.append(
            SourceManifestEntry(
                key=key,
                name=_text(raw.get("nombre"), f"{key}.nombre", required=True),
                organization=_text(raw.get("institucion"), f"{key}.institucion", required=True),
                category=_text(raw.get("categoria"), f"{key}.categoria", required=True),
                mode=mode,
                url=_validate_url(raw.get("url"), f"{key}.url"),
                license=_text(raw.get("licencia"), f"{key}.licencia", required=True),
                refresh_policy=_text(raw.get("frecuencia"), f"{key}.frecuencia", required=True),
                coverage=_text(raw.get("cobertura"), f"{key}.cobertura", required=True),
                schema_version=schema_version,
                corpus_files=tuple(corpus),
                connected=connected,
                remote_adapter=remote_adapter,
                verified_on=verified_on,
                review_before=review_before,
            )
        )
    return entries


def _normalise_text(value: object) -> str:
    """Convierte texto multilinea del YAML en una línea estable para hash/RAG."""
    return " ".join(str(value).split())


def _optional_text(raw: dict[str, object], *keys: str) -> str | None:
    """Obtiene el primer campo textual no vacío de un mapping."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return _normalise_text(value)
    return None


def _build_fact(
    *,
    source: SourceManifestEntry,
    root: dict[str, object],
    title: str,
    text: str,
    source_name: str | None = None,
    source_url: str | None = None,
    source_date: str | None = None,
    location: str | None = None,
    product: str | None = None,
) -> CorpusFact | None:
    """Construye un hecho común para documentos, reglas y sedes."""
    clean_text = _normalise_text(text)
    if not clean_text:
        return None
    verified_on = _date(root.get("verificado_el"), f"{source.key}.verificado_el")
    review_before = _date(
        root.get("revisar_antes_de") or root.get("revisar_antes"),
        f"{source.key}.revisar_antes_de",
    )
    return CorpusFact(
        source_key=source.key,
        domain=source.category,
        source_name=_normalise_text(source_name or source.organization),
        subject=_normalise_text(source_name or title),
        location=_normalise_text(location) if location else None,
        product=_normalise_text(product) if product else None,
        title=_normalise_text(title),
        text=clean_text,
        source_url=source_url or source.url,
        source_date=_normalise_text(source_date) if source_date else None,
        verified_on=verified_on or source.verified_on,
        review_before=review_before or source.review_before,
    )


def load_snapshot_facts(
    source: SourceManifestEntry,
    corpus_dir: Path,
) -> list[CorpusFact]:
    """Extrae documentos, reglas y sedes del snapshot asignado a una fuente."""
    facts: list[CorpusFact] = []
    for filename in source.corpus_files:
        path = corpus_dir / filename
        if not path.is_file():
            raise DataHubValidationError(f"No existe el corpus {filename} de {source.key}")
        root = _load_yaml_mapping(path)

        documents = root.get("documentos", [])
        if isinstance(documents, list):
            for document in documents:
                if not isinstance(document, dict):
                    continue
                title = _text(document.get("titulo"), f"{filename}.titulo") or source.name
                source_name = _optional_text(document, "fuente") or source.organization
                source_url = _optional_text(document, "fuente_url", "url")
                source_date = _optional_text(document, "fecha")
                product = _optional_text(document, "producto", "cultivo")
                location = _optional_text(document, "comuna", "zona", "region")
                chunks = document.get("chunks", [])
                if not isinstance(chunks, list):
                    continue
                for chunk in chunks:
                    if isinstance(chunk, str):
                        chunk_text = chunk
                        chunk_url = source_url
                    elif isinstance(chunk, dict):
                        chunk_text = chunk.get("texto", "")
                        chunk_url = _optional_text(chunk, "fuente_url", "url") or source_url
                    else:
                        continue
                    fact = _build_fact(
                        source=source,
                        root=root,
                        title=title,
                        text=str(chunk_text),
                        source_name=source_name,
                        source_url=chunk_url,
                        source_date=source_date,
                        location=location,
                        product=product,
                    )
                    if fact:
                        facts.append(fact)

        rules = root.get("reglas", [])
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                crop = _optional_text(rule, "cultivo", "producto") or ""
                rule_id = _optional_text(rule, "id") or crop or "regla"
                pieces = [
                    f"Cultivo: {crop}" if crop else "",
                    f"Zona: {_optional_text(rule, 'zona')}" if _optional_text(rule, "zona") else "",
                    f"Siembra: {_optional_text(rule, 'siembra')}" if _optional_text(rule, "siembra") else "",
                    f"Cosecha: {_optional_text(rule, 'cosecha')}" if _optional_text(rule, "cosecha") else "",
                    _optional_text(rule, "diagnostico") or "",
                    f"Siguiente paso: {_optional_text(rule, 'siguiente_paso')}"
                    if _optional_text(rule, "siguiente_paso")
                    else "",
                ]
                fact = _build_fact(
                    source=source,
                    root=root,
                    title=f"Regla {rule_id}",
                    text=" ".join(piece for piece in pieces if piece),
                    source_name=_optional_text(rule, "fuente") or source.organization,
                    source_url=_optional_text(rule, "fuente_url", "url") or source.url,
                    source_date=_optional_text(rule, "fecha"),
                    location=_optional_text(rule, "zona"),
                    product=crop or None,
                )
                if fact:
                    facts.append(fact)

        offices = root.get("sedes", [])
        if isinstance(offices, list):
            for office in offices:
                if not isinstance(office, dict):
                    continue
                name = _text(office.get("nombre"), f"{filename}.nombre") or source.name
                fields = [
                    name,
                    f"Tipo: {_optional_text(office, 'tipo')}" if _optional_text(office, "tipo") else "",
                    f"Comuna: {_optional_text(office, 'comuna')}" if _optional_text(office, "comuna") else "",
                    f"Dirección: {_optional_text(office, 'direccion')}"
                    if _optional_text(office, "direccion")
                    else "",
                    f"Teléfono: {_optional_text(office, 'telefono')}"
                    if _optional_text(office, "telefono")
                    else "",
                    f"Horario: {_optional_text(office, 'horario')}"
                    if _optional_text(office, "horario")
                    else "",
                ]
                fact = _build_fact(
                    source=source,
                    root=root,
                    title=name,
                    text=" ".join(field for field in fields if field),
                    source_name=_optional_text(office, "fuente") or source.organization,
                    source_url=_optional_text(office, "fuente_url", "url") or source.url,
                    source_date=_optional_text(office, "fecha_fuente"),
                    location=_optional_text(office, "comuna"),
                )
                if fact:
                    facts.append(fact)
    return facts


def _fact_hash(fact: CorpusFact) -> str:
    """Calcula hash estable de identidad y contenido de un hecho."""
    payload = {
        "source_key": fact.source_key,
        "domain": fact.domain,
        "subject": fact.subject,
        "location": fact.location,
        "product": fact.product,
        "title": fact.title,
        "text": fact.text,
        "source_url": fact.source_url,
        "source_date": fact.source_date,
        "verified_on": fact.verified_on.isoformat(),
        "review_before": fact.review_before.isoformat() if fact.review_before else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_status(
    entry: SourceManifestEntry,
    *,
    today: datetime.date,
    persisted: DataSource | None,
) -> str:
    """Calcula el estado público sin convertir metadata en dato live."""
    if not entry.connected:
        return "not_connected"
    if today > entry.review_before:
        return "stale"
    if persisted is not None:
        if persisted.status == "disabled":
            return "disabled"
        if persisted.status == "error":
            return "error"
    if entry.mode == "live":
        return "healthy"
    if persisted is None:
        return "not_synced"
    if entry.mode == "database" and persisted.last_success_at is None:
        return "not_synced"
    if persisted.valid_until is not None and today > persisted.valid_until:
        return "stale"
    return "healthy"


def _source_row(
    entry: SourceManifestEntry,
    persisted: DataSource | None,
    *,
    today: datetime.date,
) -> dict[str, object]:
    """Convierte una fuente en un registro seguro para schemas/API."""
    status = _source_status(entry, today=today, persisted=persisted)
    return {
        "key": entry.key,
        "name": entry.name,
        "organization": entry.organization,
        "category": entry.category,
        "mode": entry.mode,
        "url": entry.url,
        "refresh_policy": entry.refresh_policy,
        "coverage": entry.coverage,
        "status": status,
        "enabled": persisted.enabled if persisted else entry.connected,
        "verified_on": entry.verified_on,
        "review_before": entry.review_before,
        "last_success_at": persisted.last_success_at if persisted else None,
        "record_count": persisted.record_count if persisted else 0,
    }


def sync_data_hub(
    db: Session,
    *,
    manifest_path: str | Path | None = None,
    corpus_dir: str | Path | None = None,
    today: datetime.date | None = None,
) -> DataHubSyncResult:
    """Valida y sincroniza el catálogo y snapshots locales de forma idempotente."""
    current_day = today or datetime.date.today()
    entries = load_manifest(manifest_path)
    root = Path(corpus_dir) if corpus_dir else _DEFAULT_CORPUS_DIR
    facts_by_source: dict[str, list[CorpusFact]] = {}
    for entry in entries:
        facts = load_snapshot_facts(entry, root) if entry.corpus_files else []
        if entry.connected and entry.mode == "snapshot" and not facts:
            raise DataHubValidationError(f"Fuente snapshot sin hechos: {entry.key}")
        facts_by_source[entry.key] = facts

    now = _utcnow_naive()
    stale_sources: list[str] = []
    not_connected_sources: list[str] = []
    facts_synced = 0

    try:
        for entry in entries:
            persisted = db.scalar(select(DataSource).where(DataSource.key == entry.key))
            facts = facts_by_source[entry.key]
            status = _source_status(entry, today=current_day, persisted=persisted)
            if current_day > entry.review_before and entry.connected:
                stale_sources.append(entry.key)
            if not entry.connected:
                not_connected_sources.append(entry.key)

            if persisted is None:
                persisted = DataSource(
                    key=entry.key,
                    name=entry.name,
                    organization=entry.organization,
                    category=entry.category,
                    mode=entry.mode,
                    url=entry.url,
                    license=entry.license,
                    refresh_policy=entry.refresh_policy,
                    coverage=entry.coverage,
                    schema_version=entry.schema_version,
                    enabled=entry.connected,
                    status=status,
                    verified_on=entry.verified_on,
                    review_before=entry.review_before,
                    record_count=0,
                )
                db.add(persisted)
            else:
                persisted.name = entry.name
                persisted.organization = entry.organization
                persisted.category = entry.category
                persisted.mode = entry.mode
                persisted.url = entry.url
                persisted.license = entry.license
                persisted.refresh_policy = entry.refresh_policy
                persisted.coverage = entry.coverage
                persisted.schema_version = entry.schema_version
                persisted.verified_on = entry.verified_on
                persisted.review_before = entry.review_before
                persisted.valid_until = entry.review_before
                persisted.status = status
                persisted.enabled = entry.connected

            persisted.last_attempt_at = now
            persisted.valid_until = entry.review_before
            if facts:
                db.query(DataFact).filter(DataFact.source_key == entry.key).delete(
                    synchronize_session=False
                )
                for fact in facts:
                    db.add(
                        DataFact(
                            source_key=fact.source_key,
                            domain=fact.domain,
                            subject=fact.subject,
                            location=fact.location,
                            product=fact.product,
                            title=fact.title,
                            text=fact.text,
                            source_url=fact.source_url,
                            source_date=fact.source_date,
                            verified_on=fact.verified_on,
                            review_before=fact.review_before,
                            fact_hash=_fact_hash(fact),
                        )
                    )
                persisted.record_count = len(facts)
                persisted.content_hash = hashlib.sha256(
                    "".join(sorted(_fact_hash(fact) for fact in facts)).encode("ascii")
                ).hexdigest()
                facts_synced += len(facts)
            elif entry.mode == "snapshot" and entry.connected:
                persisted.status = "error"
                persisted.last_error_code = "empty_snapshot"
                raise DataHubValidationError(f"Snapshot vacío para {entry.key}")

            if entry.connected:
                # Los snapshots sí confirman una carga local; una fuente de
                # base de datos (ODEPA) solo se marca exitosa tras su sync
                # real, para no confundir el corpus de referencia con precios
                # actuales.
                if entry.mode != "database":
                    persisted.last_success_at = now
                if facts or entry.mode == "snapshot":
                    remote_error = entry.remote_adapter is not None and persisted.status == "error"
                    if remote_error:
                        # El corpus local aporta contexto, pero no demuestra que
                        # el endpoint remoto siga disponible después de fallar.
                        persisted.status = "error"
                    else:
                        persisted.status = (
                            "stale" if current_day > entry.review_before else "healthy"
                        )
                        persisted.last_error_code = None
                else:
                    # Un adaptador remoto puede haber dejado un error en una
                    # fuente live; el sync local no debe ocultarlo.
                    persisted.status = status
            else:
                persisted.last_error_code = None

        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(
        "Data Hub sincronizado: fuentes=%d hechos=%d stale=%d no_conectadas=%d",
        len(entries),
        facts_synced,
        len(stale_sources),
        len(not_connected_sources),
    )
    return DataHubSyncResult(
        sources_synced=len(entries),
        facts_synced=facts_synced,
        stale_sources=tuple(stale_sources),
        not_connected_sources=tuple(not_connected_sources),
    )


def get_data_hub_status(
    db: Session,
    *,
    manifest_path: str | Path | None = None,
    today: datetime.date | None = None,
) -> dict[str, object]:
    """Entrega estado/catálogo calculado sin exponer errores internos."""
    current_day = today or datetime.date.today()
    entries = load_manifest(manifest_path)
    persisted = {
        source.key: source
        for source in db.scalars(select(DataSource)).all()
    }
    rows = [_source_row(entry, persisted.get(entry.key), today=current_day) for entry in entries]
    statuses = [str(row["status"]) for row in rows]
    total_facts = int(db.scalar(select(func.count(DataFact.id))) or 0)
    return {
        "sources": rows,
        "total_sources": len(rows),
        "healthy_sources": statuses.count("healthy"),
        "stale_sources": statuses.count("stale"),
        "not_connected_sources": statuses.count("not_connected"),
        "total_facts": total_facts,
    }


def mark_data_source_success(
    db: Session,
    source_key: str,
    *,
    record_count: int,
    valid_until: datetime.date | None,
    content_hash: str | None = None,
) -> None:
    """Registra éxito de un adaptador de datos estructurados ya sincronizado."""
    try:
        source = db.scalar(select(DataSource).where(DataSource.key == source_key))
    except SQLAlchemyError:
        # La tabla puede no existir durante un rollout en que el proceso de
        # aplicación se actualizó antes de correr Alembic. El precio ya fue
        # confirmado por su servicio de dominio; el estado auxiliar no debe
        # tumbar esa sync.
        db.rollback()
        logger.warning("No se pudo actualizar estado del Data Hub — key=%s", source_key)
        return
    if source is None:
        logger.warning("Fuente estructurada no está en el manifest — key=%s", source_key)
        return
    if record_count < 0:
        raise ValueError("record_count no puede ser negativo")
    source.last_attempt_at = _utcnow_naive()
    source.last_success_at = source.last_attempt_at
    source.record_count = record_count
    source.valid_until = valid_until
    if content_hash is not None:
        source.content_hash = content_hash
    source.status = "healthy"
    source.last_error_code = None
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning("No se pudo confirmar estado del Data Hub — key=%s", source_key)


def mark_data_source_error(db: Session, source_key: str, *, error_code: str) -> None:
    """Registra un error remoto estable sin persistir mensajes ni URLs externas."""
    if not error_code or len(error_code) > 80 or not all(
        char.isalnum() or char in {"_", "-"} for char in error_code
    ):
        raise ValueError("error_code inválido")
    try:
        source = db.scalar(select(DataSource).where(DataSource.key == source_key))
    except SQLAlchemyError:
        db.rollback()
        logger.warning("No se pudo leer estado del Data Hub — key=%s", source_key)
        return
    if source is None:
        logger.warning("Fuente remota no está en el manifest — key=%s", source_key)
        return
    source.last_attempt_at = _utcnow_naive()
    source.status = "error"
    source.last_error_code = error_code
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning("No se pudo registrar error del Data Hub — key=%s", source_key)


def get_data_hub_catalog(
    db: Session,
    *,
    manifest_path: str | Path | None = None,
    today: datetime.date | None = None,
) -> list[dict[str, object]]:
    """Atajo para el catálogo público de fuentes."""
    status = get_data_hub_status(db, manifest_path=manifest_path, today=today)
    sources = status["sources"]
    if not isinstance(sources, list):
        return []
    return [row for row in sources if isinstance(row, dict)]


def get_ecosystem_for_llm() -> str:
    """Resume capacidades reales del ecosistema para una consulta de voz."""
    entries = load_manifest()
    connected = [entry.name for entry in entries if entry.connected]
    not_connected = [entry.name for entry in entries if not entry.connected]
    connected_text = ", ".join(connected)
    not_connected_text = ", ".join(not_connected)
    return (
        "AgroVoz integra precios agrícolas de ODEPA, clima de Open-Meteo, "
        "conocimiento agronómico de INIA, programas de INDAP y un directorio "
        "de oficinas y servicios agrícolas. "
        f"Fuentes conectadas en esta versión: {connected_text}. "
        f"También tiene catalogadas, pero no conectadas como dato vivo: {not_connected_text}. "
        "AgroVoz orienta y cita fuentes; no reemplaza a las instituciones, no hace trámites "
        "ni es un marketplace."
    )


def fact_to_dict(fact: DataFact) -> dict[str, object]:
    """Serializa un hecho sin campos de usuario ni metadata interna."""
    return {
        "source_key": fact.source_key,
        "domain": fact.domain,
        "subject": fact.subject,
        "location": fact.location,
        "product": fact.product,
        "title": fact.title,
        "text": fact.text,
        "source_url": fact.source_url,
        "source_date": fact.source_date,
        "verified_on": fact.verified_on,
        "review_before": fact.review_before,
        "fact_hash": fact.fact_hash,
    }


__all__ = [
    "CorpusFact",
    "DataHubSyncResult",
    "DataHubValidationError",
    "SourceManifestEntry",
    "fact_to_dict",
    "get_data_hub_catalog",
    "get_data_hub_status",
    "get_ecosystem_for_llm",
    "load_manifest",
    "load_snapshot_facts",
    "mark_data_source_error",
    "mark_data_source_success",
    "sync_data_hub",
]
