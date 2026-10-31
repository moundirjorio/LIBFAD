from anthropic import APIError, AuthenticationError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_current_user, get_session
from app.models.chat import ChatMessage, ChatThread
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.user import User
from app.schemas.chat import ChatMessageIn, ChatMessageOut, ChatThreadOut
from app.services.ai import chat_reply

router = APIRouter()


async def _build_business_context(db: AsyncSession, tenant_id: int) -> str:
    """Résumé compact des données du tenant pour donner du contexte à Claude."""
    client_count = await db.scalar(select(func.count(Client.id)).where(Client.tenant_id == tenant_id))
    invoices_total = await db.scalar(select(func.count(Invoice.id)).where(Invoice.tenant_id == tenant_id))
    overdue = await db.scalar(
        select(func.count(Invoice.id))
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.status == "overdue")
    )
    outstanding = await db.scalar(
        select(func.coalesce(func.sum(Invoice.amount_due), 0))
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.amount_due > 0)
    )
    return (
        f"Clients actifs : {client_count or 0}\n"
        f"Factures totales : {invoices_total or 0}\n"
        f"Factures en retard : {overdue or 0}\n"
        f"Montant total dû : {float(outstanding or 0):.2f}"
    )


@router.get("/threads", response_model=list[ChatThreadOut])
async def list_threads(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return list(
        await db.scalars(
            select(ChatThread)
            .where(ChatThread.tenant_id == user.tenant_id, ChatThread.user_id == user.id)
            .order_by(ChatThread.updated_at.desc())
        )
    )


@router.get("/threads/{thread_id}/messages", response_model=list[ChatMessageOut])
async def thread_messages(
    thread_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    thread = await db.scalar(
        select(ChatThread).where(
            ChatThread.id == thread_id,
            ChatThread.tenant_id == user.tenant_id,
            ChatThread.user_id == user.id,
        )
    )
    if not thread:
        raise HTTPException(404, "Conversation introuvable")
    return list(
        await db.scalars(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.id)
        )
    )


@router.post("/send", response_model=ChatMessageOut)
async def send_message(
    payload: ChatMessageIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    # Récupère ou crée le thread
    if payload.thread_id:
        thread = await db.scalar(
            select(ChatThread)
            .where(ChatThread.id == payload.thread_id)
            .where(ChatThread.tenant_id == user.tenant_id)
            .where(ChatThread.user_id == user.id)
            .options(selectinload(ChatThread.messages))
        )
        if not thread:
            raise HTTPException(404, "Conversation introuvable")
    else:
        thread = ChatThread(
            tenant_id=user.tenant_id,
            user_id=user.id,
            title=payload.content[:60],
            client_id=payload.client_id,
            invoice_id=payload.invoice_id,
        )
        db.add(thread)
        await db.flush()

    # Sauvegarde le message user
    user_msg = ChatMessage(
        tenant_id=user.tenant_id,
        thread_id=thread.id,
        role="user",
        content=payload.content,
    )
    db.add(user_msg)
    await db.flush()

    # Historique pour Claude
    history_rows = list(
        await db.scalars(
            select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.id)
        )
    )
    history = [{"role": m.role, "content": m.content} for m in history_rows if m.role in ("user", "assistant")]

    context = await _build_business_context(db, user.tenant_id)
    try:
        result = await chat_reply(history, business_context=context)
    except AuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Clé Claude invalide ou manquante. Configure ANTHROPIC_API_KEY dans .env puis redémarre le serveur.",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except APIError as exc:
        raise HTTPException(status_code=502, detail=f"Erreur API Claude : {exc}")

    assistant_msg = ChatMessage(
        tenant_id=user.tenant_id,
        thread_id=thread.id,
        role="assistant",
        content=result["text"],
        tokens_in=result.get("input_tokens"),
        tokens_out=result.get("output_tokens"),
        model=result.get("model"),
    )
    db.add(assistant_msg)
    thread.message_count = (thread.message_count or 0) + 2
    await db.commit()
    await db.refresh(assistant_msg)
    return assistant_msg
