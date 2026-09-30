"""Los dos checkpoints humanos.

Checkpoint 1 — Data QA (antes de analizar): ¿está activo? ¿es de ese competidor? ¿es variante o creativo nuevo?
Checkpoint 2 — Strategic QA (antes de Basecamp): APPROVE / EDIT / REJECT de la síntesis.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from .db import Creative, Run, Sighting, Synthesis
from .normalize import data_qa_flags


def data_qa_queue(session: Session, run: Run) -> list[dict[str, Any]]:
    """Creativos vistos en este run que merecen una mirada humana."""
    ids = [s.creative_id for s in session.query(Sighting).filter_by(run_id=run.id).all()]
    out = []
    for c in session.query(Creative).filter(Creative.id.in_(ids)).all():
        flags = data_qa_flags(c)
        cls = c.latest_classification
        if cls and cls.confidence == "low":
            flags.append("clasificacion_baja_confianza")
        if flags and c.qa_status == "pending":
            out.append(
                {
                    "id": c.id,
                    "competitor": c.competitor.name,
                    "platform": c.platform,
                    "source": c.source,
                    "days_active": c.days_active,
                    "headline": c.headline[:80],
                    "flags": flags,
                    "snapshot_url": c.snapshot_url,
                }
            )
    return sorted(out, key=lambda r: (-len(r["flags"]), r["competitor"]))


def set_qa_status(session: Session, creative_ids: list[int], status: str, notes: str = "") -> int:
    assert status in ("ok", "flagged", "rejected", "pending")
    n = 0
    for c in session.query(Creative).filter(Creative.id.in_(creative_ids)).all():
        c.qa_status = status
        if notes:
            c.qa_notes = notes
        n += 1
    session.flush()
    return n


def approve_all_pending(session: Session, run: Run) -> int:
    ids = [s.creative_id for s in session.query(Sighting).filter_by(run_id=run.id).all()]
    n = 0
    for c in session.query(Creative).filter(Creative.id.in_(ids), Creative.qa_status == "pending").all():
        c.qa_status = "ok"
        n += 1
    session.flush()
    return n


def latest_synthesis(session: Session, run: Run) -> Synthesis | None:
    return session.query(Synthesis).filter_by(run_id=run.id).order_by(Synthesis.created_at.desc()).first()


def review_synthesis(session: Session, run: Run, decision: str, reviewer: str, notes: str = "", edited: dict[str, Any] | None = None) -> Synthesis:
    """decision: approve | edit | reject. `edit` guarda una nueva versión aprobada con los cambios."""
    current = latest_synthesis(session, run)
    if current is None:
        raise ValueError("No hay síntesis para revisar; correr `synthesize` primero")
    now = dt.datetime.now(dt.timezone.utc)
    if decision == "edit":
        if not edited:
            raise ValueError("edit requiere el JSON editado")
        current = Synthesis(run_id=run.id, model=f"{current.model}+human", data=edited, status="approved")
        session.add(current)
        decision = "approved"
    elif decision == "approve":
        decision = "approved"
    elif decision == "reject":
        decision = "rejected"
    else:
        raise ValueError("decision debe ser approve | edit | reject")
    current.status = decision
    current.reviewer = reviewer
    current.review_notes = notes
    current.reviewed_at = now
    run.status = "approved" if decision == "approved" else "draft"
    session.flush()
    return current
