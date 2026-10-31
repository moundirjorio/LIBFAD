from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class DunningScenario(Base, TenantMixin, TimestampMixin):
    """Scénario de relance configurable."""

    __tablename__ = "dunning_scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    steps: Mapped[list["DunningStep"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="DunningStep.order_index"
    )


class DunningStep(Base, TenantMixin, TimestampMixin):
    """Étape d'un scénario : J+N envoie email avec tel ton."""

    __tablename__ = "dunning_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("dunning_scenarios.id", ondelete="CASCADE"), index=True
    )

    order_index: Mapped[int] = mapped_column(Integer, default=0)
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)  # depuis la due date
    channel: Mapped[str] = mapped_column(String(20), default="email")  # email|sms|call|escalation
    tone: Mapped[str] = mapped_column(String(20), default="polite")
    # polite | firm | final | legal | friendly
    subject_template: Mapped[str | None] = mapped_column(String(300))
    body_template: Mapped[str | None] = mapped_column(Text)
    escalate_to: Mapped[str | None] = mapped_column(String(40))  # accounting|legal|none
    locale: Mapped[str | None] = mapped_column(String(10))  # null = utilise locale client

    scenario: Mapped["DunningScenario"] = relationship(back_populates="steps")


class DunningRun(Base, TenantMixin, TimestampMixin):
    """Exécution d'un scénario sur une facture donnée."""

    __tablename__ = "dunning_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("dunning_scenarios.id", ondelete="RESTRICT"))

    status: Mapped[str] = mapped_column(String(20), default="running")
    # running | paused | stopped | completed | resolved
    current_step_index: Mapped[int] = mapped_column(Integer, default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[date] = mapped_column(Date, default=date.today)
    resolved_at: Mapped[date | None] = mapped_column(Date)

    messages: Mapped[list["DunningMessage"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="DunningMessage.sent_at"
    )


class DunningMessage(Base, TenantMixin, TimestampMixin):
    """Message envoyé (ou prévu) dans le cadre d'une relance."""

    __tablename__ = "dunning_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("dunning_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("dunning_steps.id", ondelete="SET NULL"))

    channel: Mapped[str] = mapped_column(String(20), default="email")
    direction: Mapped[str] = mapped_column(String(10), default="outbound")  # outbound|inbound
    to_email: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(String(10))
    tone: Mapped[str | None] = mapped_column(String(20))

    status: Mapped[str] = mapped_column(String(20), default="queued")
    # queued | sent | failed | replied | opened
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_prompt: Mapped[str | None] = mapped_column(Text)

    run: Mapped["DunningRun"] = relationship(back_populates="messages")
