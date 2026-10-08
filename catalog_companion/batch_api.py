"""Authenticated batch/workflow routes, installed on the existing v1 router."""
from fastapi import Header, HTTPException, Query

from .batch_models import BatchAction, BatchPreview
from .batches import BatchConflict
from .engine import EngineError


def install(router, app):
    def call(function, *args):
        try:
            return function(*args)
        except KeyError as exc:
            raise HTTPException(404, "Batch not found") from exc
        except BatchConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except EngineError as exc:
            raise HTTPException(401 if exc.auth else 503, str(exc)) from exc

    @router.get("/workflows")
    def workflows(authorization: str = Header(), search: str = "", offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return call(app.state.batches.engine.workflows, authorization, search, offset, limit)

    @router.get("/workflows/{name}")
    def workflow(name: str, authorization: str = Header()):
        return call(app.state.batches.engine.workflow, authorization, name)

    @router.post("/scan-batches", status_code=201)
    def preview(payload: BatchPreview, authorization: str = Header()):
        return call(app.state.batches.prepare, payload, authorization)

    @router.get("/scan-batches")
    def batches():
        return {"items": [{key: value for key, value in batch.items() if key not in ("manifest", "request", "history")}
                          for batch in app.state.batches.list()[:50]]}

    @router.get("/scan-batches/{batch_id}")
    def batch(batch_id: str):
        return call(lambda: app.state.batches.view(app.state.batches.get(batch_id)))

    @router.post("/scan-batches/{batch_id}/{action}", status_code=202)
    def action(batch_id: str, action: str, payload: BatchAction, authorization: str = Header()):
        return call(app.state.batches.action, batch_id, action, payload, authorization)

    @router.post("/scan-batches/{batch_id}/items/{item_id}/{action}")
    def item_action(batch_id: str, item_id: str, action: str, payload: BatchAction):
        return call(app.state.batches.item_action, batch_id, item_id, action, payload)
