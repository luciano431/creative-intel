"""API HTTP mínima para que Make (u otro orquestador) dispare pasos y consulte estado.

    uvicorn creative_intel.api:app --port 8000

Auth: header `X-API-Token: <API_TOKEN>`. Los pasos largos corren en background y devuelven 202.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import pipeline
from .config import settings
from .db import get_or_create_run, init_db, session_scope
from .review import data_qa_queue, latest_synthesis, review_synthesis, set_qa_status

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="creative-intel", version="0.1.0", lifespan=_lifespan)


def auth(x_api_token: Optional[str] = Header(default=None)) -> None:
    if not settings.api_token or x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="token inválido")


class StepOptions(BaseModel):
    mock_ai: bool = False
    sources: list[str] | None = None
    skip_collect: bool = False


class QaUpdate(BaseModel):
    ids: list[int]
    status: str  # ok | flagged | rejected
    notes: str = ""


class ReviewDecision(BaseModel):
    decision: str  # approve | edit | reject
    reviewer: str
    notes: str = ""
    edited: dict[str, Any] | None = None


def _bg(fn, *args, **kwargs):
    def runner():
        with session_scope() as s:
            fn(s, *args, **kwargs)

    return runner


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runs/{period}", dependencies=[Depends(auth)])
def run_status(period: str) -> dict[str, Any]:
    with session_scope() as s:
        run = get_or_create_run(s, period)
        synth = latest_synthesis(s, run)
        return {
            "period": run.period,
            "status": run.status,
            "stats": run.stats,
            "totals": (run.metrics or {}).get("totals"),
            "synthesis_status": synth.status if synth else None,
            "data_qa_pending": len(data_qa_queue(s, run)),
        }


@app.post("/runs/{period}/collect", status_code=202, dependencies=[Depends(auth)])
def step_collect(period: str, opts: StepOptions, bg: BackgroundTasks) -> dict[str, str]:
    bg.add_task(_bg(pipeline.collect, period, sources=opts.sources))
    return {"accepted": "collect", "period": period}


@app.post("/runs/{period}/classify", status_code=202, dependencies=[Depends(auth)])
def step_classify(period: str, opts: StepOptions, bg: BackgroundTasks) -> dict[str, str]:
    bg.add_task(_bg(pipeline.classify, period, mock=opts.mock_ai))
    return {"accepted": "classify", "period": period}


@app.post("/runs/{period}/analyze", dependencies=[Depends(auth)])
def step_analyze(period: str) -> dict[str, Any]:
    with session_scope() as s:
        return {"totals": pipeline.analyze(s, period)["totals"]}


@app.post("/runs/{period}/synthesize", status_code=202, dependencies=[Depends(auth)])
def step_synthesize(period: str, opts: StepOptions, bg: BackgroundTasks) -> dict[str, str]:
    bg.add_task(_bg(pipeline.synthesize_run, period, mock=opts.mock_ai))
    return {"accepted": "synthesize", "period": period}


@app.post("/runs/{period}/report", dependencies=[Depends(auth)])
def step_report(period: str) -> dict[str, str]:
    with session_scope() as s:
        return {k: str(v) for k, v in pipeline.build_report(s, period).items()}


@app.post("/runs/{period}/cycle", status_code=202, dependencies=[Depends(auth)])
def step_cycle(period: str, opts: StepOptions, bg: BackgroundTasks) -> dict[str, str]:
    bg.add_task(_bg(pipeline.run_cycle, period, mock_ai=opts.mock_ai, skip_collect=opts.skip_collect, sources=opts.sources))
    return {"accepted": "cycle", "period": period}


@app.get("/runs/{period}/qa", dependencies=[Depends(auth)])
def qa_queue(period: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        return data_qa_queue(s, get_or_create_run(s, period))


@app.post("/runs/{period}/qa", dependencies=[Depends(auth)])
def qa_update(period: str, body: QaUpdate) -> dict[str, int]:
    with session_scope() as s:
        return {"updated": set_qa_status(s, body.ids, body.status, body.notes)}


@app.get("/runs/{period}/synthesis", dependencies=[Depends(auth)])
def get_synthesis(period: str) -> dict[str, Any]:
    with session_scope() as s:
        synth = latest_synthesis(s, get_or_create_run(s, period))
        if not synth:
            raise HTTPException(404, "sin síntesis")
        return {"id": synth.id, "status": synth.status, "model": synth.model, "data": synth.data}


@app.post("/runs/{period}/review", dependencies=[Depends(auth)])
def post_review(period: str, body: ReviewDecision) -> dict[str, Any]:
    with session_scope() as s:
        synth = review_synthesis(s, get_or_create_run(s, period), body.decision, body.reviewer, body.notes, edited=body.edited)
        return {"id": synth.id, "status": synth.status}


@app.post("/runs/{period}/publish", dependencies=[Depends(auth)])
def step_publish(period: str, force: bool = False) -> dict[str, Any]:
    with session_scope() as s:
        try:
            return pipeline.publish(s, period, force=force)
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
