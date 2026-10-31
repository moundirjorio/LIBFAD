from pydantic import BaseModel


class DunningStepIn(BaseModel):
    offset_days: int
    channel: str = "email"
    tone: str = "polite"
    subject_template: str | None = None
    body_template: str | None = None
    escalate_to: str | None = None
    locale: str | None = None
    order_index: int = 0


class DunningStepOut(DunningStepIn):
    id: int

    class Config:
        from_attributes = True


class DunningScenarioIn(BaseModel):
    name: str
    description: str | None = None
    is_default: bool = False
    steps: list[DunningStepIn] = []


class DunningScenarioOut(BaseModel):
    id: int
    name: str
    description: str | None
    is_default: bool
    is_active: bool
    steps: list[DunningStepOut] = []

    class Config:
        from_attributes = True


class DunningMessageOut(BaseModel):
    id: int
    channel: str
    to_email: str | None
    subject: str | None
    body: str | None
    locale: str | None
    tone: str | None
    status: str
    sent_at: str | None = None
    ai_generated: bool

    class Config:
        from_attributes = True


class GenerateReminderIn(BaseModel):
    invoice_id: int
    tone: str = "polite"
    locale: str | None = None
    custom_instructions: str | None = None
