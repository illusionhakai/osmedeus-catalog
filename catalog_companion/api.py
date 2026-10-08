from __future__ import annotations

import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bugbounty_catalog.application import CatalogService
from bugbounty_catalog.query import AssetQuery, ProgramQuery
from bugbounty_catalog.sources import available_sources
from .exports import create_bundle, preview_bundle
from .state import RefreshManager, State
from .delivery import DeliveryManager, DeliveryBusy
from . import __version__
from .batches import BatchManager
from .batch_api import install as install_batch_routes


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    time: str = "03:00"
    timezone: str = "America/Sao_Paulo"
    include_vdp: bool = False

    @field_validator("time")
    @classmethod
    def valid_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Time must be HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown timezone") from exc
        return value


class Refresh(BaseModel):
    source: str = "all"
    include_vdp: bool = False

    @field_validator("source")
    @classmethod
    def valid_source(cls, value):
        if value not in ("all", *available_sources()):
            raise ValueError("Unknown source")
        return value


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: dict = Field(default_factory=dict)
    platform: str | None = None
    asset_ids: list[int] = Field(default_factory=list, max_length=10000)
    convert_wildcards: bool = False

    @field_validator("filters")
    @classmethod
    def valid_filters(cls, value):
        allowed = {"q", "target_text", "program_id", "source", "scope", "kind", "active"}
        if set(value) - allowed:
            raise ValueError("Unknown export filter")
        if any(v is not None and not isinstance(v, (str, int, bool)) for v in value.values()):
            raise ValueError("Export filters must be scalar values")
        AssetQuery.from_mapping(value)
        return value


class DeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry: bool = False


class EngineAuth:
    def __init__(self, url: str, transport=None):
        self.url = url.rstrip("/") + "/osm/api/runs?limit=1"
        self.transport = transport

    async def __call__(self, authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
            raise HTTPException(401, "Osmedeus login required")
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False, transport=self.transport,
                                         trust_env=False) as client:
                # Detect a no-auth engine: an unauthenticated 200 must never authenticate the companion.
                probe = await client.get(self.url, headers={"Authorization": "Bearer invalid-catalog-probe"})
                if probe.status_code not in (401, 403):
                    raise HTTPException(503, "Osmedeus authentication compatibility check failed")
                response = await client.get(self.url, headers={"Authorization": authorization})
            if response.status_code in (401, 403):
                raise HTTPException(401, "Osmedeus session expired or invalid")
            if response.status_code != 200:
                raise HTTPException(503, "Osmedeus authentication is unavailable")
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("pagination"), dict):
                raise HTTPException(503, "Unexpected Osmedeus authentication response")
            total = body["pagination"].get("total")
            if type(total) is not int or total < 0:
                raise HTTPException(503, "Unexpected Osmedeus authentication response")
            # Go encodes a nil slice as null when this installation has no runs.
            empty_runs = "data" in body and body["data"] is None and total == 0
            if not isinstance(body.get("data"), list) and not empty_runs:
                raise HTTPException(503, "Unexpected Osmedeus authentication response")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "Osmedeus authentication is unavailable") from exc


def create_app(data_dir=None, engine_url=None, *, transport=None, background=None):
    data = Path(data_dir or os.getenv("CATALOG_DATA_DIR", "data"))
    engine = engine_url or os.getenv("OSMEDEUS_URL", "http://host.docker.internal:8002")
    auth = EngineAuth(engine, transport)
    if background is None:
        background = os.getenv("CATALOG_BACKGROUND", "true").lower() == "true"

    @asynccontextmanager
    async def lifespan(app):
        data.mkdir(parents=True, exist_ok=True)
        ownership = FileLock(str(data / "companion.lock"), timeout=0)
        with ownership:
            db_path = data / "catalog.sqlite"
            # SQLite backup API captures WAL contents too; never copy a live DB file.
            if db_path.exists():
                backups = data / "backups"
                backups.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                with sqlite3.connect(db_path) as source, sqlite3.connect(backups / f"catalog-{stamp}.sqlite") as destination:
                    source.backup(destination)
            service = CatalogService(db_path, expose_recon_actions=False)
            service.initialize()
            state = State(data / "control.sqlite")
            manager = RefreshManager(service, state)
            delivery = DeliveryManager(state, engine, transport)
            batches = BatchManager(state, data, delivery, transport)
            app.state.batches = batches
            batches.thread.start()
            app.state.delivery = delivery
            app.state.catalog, app.state.control, app.state.manager = service, state, manager
            if background:
                if service.stats()["programs"] == 0:
                    manager.submit(include_vdp=state.get("schedule")["include_vdp"])
                manager.thread.start()
            try:
                yield
            finally:
                batches.close()
                manager.close()
                delivery.close()

    app = FastAPI(title="Osmedeus Catalog", version=__version__, lifespan=lifespan)

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    router = APIRouter(prefix="/catalog-api/v1", dependencies=[Depends(auth)])

    @router.get("/overview")
    def overview():
        result = app.state.catalog.overview()
        with sqlite3.connect(data / "catalog.sqlite") as db:
            db.row_factory = sqlite3.Row
            result["sources"] = []
            for source in available_sources():
                latest = db.execute("SELECT * FROM source_runs WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
                success = db.execute("SELECT completed_at FROM source_runs WHERE source=? AND status='completed' ORDER BY id DESC LIMIT 1", (source,)).fetchone()
                completed = success[0] if success else None
                result["sources"].append({"name": source, "latest": dict(latest) if latest else None,
                    "last_success": completed, "age_seconds": (datetime.now(timezone.utc) - datetime.fromisoformat(completed)).total_seconds() if completed else None})
        return result

    @router.get("/programs")
    def programs(request: Request):
        return app.state.catalog.programs(ProgramQuery.from_mapping(dict(request.query_params)))

    @router.get("/programs/{program_id}")
    def program(program_id: int):
        result = app.state.catalog.program(program_id)
        if result is None:
            raise HTTPException(404, "Program not found")
        return result

    @router.get("/assets")
    def assets(request: Request):
        return app.state.catalog.assets(AssetQuery.from_mapping(dict(request.query_params)))

    @router.post("/refresh", status_code=202)
    def refresh(payload: Refresh):
        return {"job_id": app.state.manager.submit(payload.source, payload.include_vdp)}

    @router.get("/jobs")
    def jobs():
        return {"items": app.state.control.jobs()}

    @router.get("/schedule")
    def schedule():
        return app.state.control.get("schedule")

    @router.put("/schedule")
    def update_schedule(payload: Schedule):
        app.state.control.set("schedule", payload.model_dump())
        return payload

    @router.post("/exports", status_code=201)
    def export(payload: ExportRequest):
        return create_bundle(data / "catalog.sqlite", data / "exports", payload.model_dump())

    def export_path(export_id):
        if not re.fullmatch("[a-f0-9]{32}", export_id):
            raise HTTPException(404, "Export not found")
        path = data / "exports" / f"{export_id}.zip"
        if not path.is_file():
            raise HTTPException(404, "Export not found")
        return path

    @router.get("/exports/{export_id}")
    def preview(export_id: str):
        return preview_bundle(export_path(export_id))

    @router.get("/exports/{export_id}/download")
    def download(export_id: str):
        return FileResponse(export_path(export_id), media_type="application/zip", filename=f"catalog-{export_id}.zip")

    @router.post("/exports/{export_id}/deliver", status_code=202)
    def deliver(export_id: str, payload: DeliveryRequest, authorization: str = Header()):
        try:
            return app.state.delivery.submit(export_path(export_id), authorization, payload.retry)
        except DeliveryBusy as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/exports/{export_id}/delivery")
    def delivery_status(export_id: str):
        export_path(export_id)
        receipt = app.state.delivery.get(export_id)
        if receipt is None:
            raise HTTPException(404, "This export has not been sent to Osmedeus")
        return receipt

    @router.get("/deliveries")
    def deliveries():
        return {"items": app.state.delivery.list()[:50]}

    install_batch_routes(router, app)
    app.include_router(router)
    return app


app = create_app()
