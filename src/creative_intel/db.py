"""Modelo de datos (SQLAlchemy 2.0). SQLite en el MVP; Postgres cambiando DATABASE_URL."""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Competitor(Base):
    __tablename__ = "competitors"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(4))
    tier: Mapped[str] = mapped_column(String(8))  # local | global | own
    website: Mapped[str] = mapped_column(String(256), default="")
    meta_page_id: Mapped[str] = mapped_column(String(64), default="")
    google_advertiser_id: Mapped[str] = mapped_column(String(64), default="")
    ad_countries: Mapped[list] = mapped_column(JSON, default=list)
    products: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    creatives: Mapped[list["Creative"]] = relationship(back_populates="competitor")

    @property
    def is_own(self) -> bool:
        return self.tier == "own"


class Run(Base):
    """Un ciclo mensual. period = 'YYYY-MM' del mes analizado."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="created")
    # created -> collected -> classified -> analyzed -> draft -> approved -> published
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class Creative(Base):
    __tablename__ = "creatives"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_creative_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    platform: Mapped[str] = mapped_column(String(16))  # meta | google
    source: Mapped[str] = mapped_column(String(32))  # meta_api | meta_web | google_serpapi | manual ...
    external_id: Mapped[str] = mapped_column(String(128), default="")
    format: Mapped[str] = mapped_column(String(24), default="desconocido")
    placements: Mapped[list] = mapped_column(JSON, default=list)
    headline: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    cta_text: Mapped[str] = mapped_column(String(128), default="")
    landing_url: Mapped[str] = mapped_column(Text, default="")
    media_urls: Mapped[list] = mapped_column(JSON, default=list)
    thumbnail_path: Mapped[str] = mapped_column(Text, default="")
    snapshot_url: Mapped[str] = mapped_column(Text, default="")
    countries: Mapped[list] = mapped_column(JSON, default=list)
    engagement: Mapped[dict] = mapped_column(JSON, default=dict)  # señales visibles, no estimaciones
    first_seen: Mapped[dt.date] = mapped_column(Date)
    last_seen: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | inactive
    qa_status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | ok | flagged | rejected
    qa_notes: Mapped[str] = mapped_column(Text, default="")
    concept_id: Mapped[int | None] = mapped_column(ForeignKey("concepts.id"), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    competitor: Mapped[Competitor] = relationship(back_populates="creatives")
    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="creative", order_by="Classification.created_at.desc()", cascade="all, delete-orphan"
    )
    sightings: Mapped[list["Sighting"]] = relationship(back_populates="creative", cascade="all, delete-orphan")

    @property
    def days_active(self) -> int:
        return max(0, (self.last_seen - self.first_seen).days)

    @property
    def latest_classification(self) -> "Classification | None":
        return self.classifications[0] if self.classifications else None


class Sighting(Base):
    """Cada vez que un run observa un creativo. Base del histórico y de la longevidad."""

    __tablename__ = "sightings"
    __table_args__ = (UniqueConstraint("creative_id", "run_id", name="uq_sighting_creative_run"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    creative_id: Mapped[int] = mapped_column(ForeignKey("creatives.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    seen_on: Mapped[dt.date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    creative: Mapped[Creative] = relationship(back_populates="sightings")


class Classification(Base):
    """Salida de la Etapa 1 (IA). `data` respeta la taxonomía cerrada."""

    __tablename__ = "classifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    creative_id: Mapped[int] = mapped_column(ForeignKey("creatives.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    model: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(8), default="medium")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    creative: Mapped[Creative] = relationship(back_populates="classifications")


class Concept(Base):
    """Familia creativa: varios anuncios que comparten un mismo concepto."""

    __tablename__ = "concepts"
    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    key: Mapped[str] = mapped_column(String(160), default="")  # producto|angulo|hook
    first_seen: Mapped[dt.date] = mapped_column(Date)
    last_seen: Mapped[dt.date] = mapped_column(Date)


class Synthesis(Base):
    """Salida de la Etapa 2 (IA) + estado de la validación humana estratégica."""

    __tablename__ = "syntheses"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    model: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | approved | rejected
    reviewer: Mapped[str] = mapped_column(String(128), default="")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Publication(Base):
    __tablename__ = "publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    channel: Mapped[str] = mapped_column(String(32))
    external_url: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------- engine
_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(url: str | None = None):
    global _engine, _SessionLocal
    if _engine is None or url:
        target = url or settings.database_url
        if target.startswith("sqlite:///"):
            from pathlib import Path

            db_path = target.replace("sqlite:///", "", 1)
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {}
        if target.endswith(":memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        _engine = create_engine(target, future=True, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    get_engine(url)
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_or_create_run(session: Session, period: str) -> Run:
    run = session.query(Run).filter_by(period=period).one_or_none()
    if run is None:
        run = Run(period=period)
        session.add(run)
        session.flush()
    return run


def sync_competitors(session: Session, configs: list[Any]) -> int:
    """Refleja config/competitors.yaml en la tabla competitors. Devuelve cantidad sincronizada."""
    n = 0
    for c in configs:
        row = session.query(Competitor).filter_by(slug=c.slug).one_or_none()
        if row is None:
            row = Competitor(slug=c.slug)
            session.add(row)
        row.name = c.name
        row.country = c.country
        row.tier = c.tier
        row.website = c.website
        row.meta_page_id = c.meta_page_id
        row.google_advertiser_id = c.google_advertiser_id
        row.ad_countries = c.ad_countries
        row.products = c.products
        row.active = c.active
        n += 1
    session.flush()
    return n


def purge_creatives(session: Session, platform: str | None = None, slugs: list[str] | None = None, source: str | None = None) -> dict[str, int]:
    """Borra creativos junto con sus clasificaciones y avistamientos, y limpia huérfanos viejos."""
    from sqlalchemy import delete, select

    q = select(Creative)
    if platform:
        q = q.where(Creative.platform == platform)
    if source:
        q = q.where(Creative.source == source)
    if slugs:
        q = q.join(Competitor).where(Competitor.slug.in_(slugs))
    rows = list(session.scalars(q))
    for c in rows:
        session.delete(c)
    session.flush()
    alive = select(Creative.id)
    orphan_cls = session.execute(delete(Classification).where(Classification.creative_id.not_in(alive))).rowcount or 0
    orphan_sig = session.execute(delete(Sighting).where(Sighting.creative_id.not_in(alive))).rowcount or 0
    return {"creatives": len(rows), "orphan_classifications": orphan_cls, "orphan_sightings": orphan_sig}
