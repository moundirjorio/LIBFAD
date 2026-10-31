from pydantic import BaseModel


class ChatMessageIn(BaseModel):
    content: str
    thread_id: int | None = None
    client_id: int | None = None
    invoice_id: int | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    model: str | None = None

    class Config:
        from_attributes = True


class ChatThreadOut(BaseModel):
    id: int
    title: str
    summary: str | None
    message_count: int

    class Config:
        from_attributes = True
