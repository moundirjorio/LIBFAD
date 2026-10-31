from datetime import date as _date
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_user, get_session
from app.models.document import Document
from app.models.user import User
from app.schemas.document import DocumentOut
from app.services.ocr import extract_invoice_data

router = APIRouter()


def _safe_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in (".", "-", "_")) or "document"


def _parse_decimal(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _parse_date(v) -> _date | None:
    if not v:
        return None
    try:
        return _date.fromisoformat(str(v))
    except ValueError:
        return None


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    return list(
        await db.scalars(
            select(Document).where(Document.tenant_id == user.tenant_id).order_by(Document.id.desc())
        )
    )


async def _store_upload(
    db: AsyncSession,
    user: User,
    file: UploadFile,
    kind: str,
    client_id: int | None,
) -> Document:
    upload_dir = settings.storage_path / "documents" / f"tenant_{user.tenant_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(file.filename or "document")
    target = upload_dir / f"{int(_date.today().toordinal())}-{safe}"
    content = await file.read()
    target.write_bytes(content)

    doc = Document(
        tenant_id=user.tenant_id,
        client_id=client_id,
        kind=kind,
        original_filename=file.filename or safe,
        file_path=str(target),
        mime_type=file.content_type,
        file_size=len(content),
        ocr_status="pending",
        status="new",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def _process_ocr(db: AsyncSession, doc: Document) -> None:
    """Lance l'OCR sur un document et met à jour ses champs. Lève en cas d'échec
    (le statut 'failed' est persisté avant la propagation de l'exception)."""
    try:
        doc.ocr_status = "processing"
        await db.flush()

        data = await extract_invoice_data(Path(doc.file_path))
        doc.supplier_name = data.get("supplier_name")
        doc.supplier_vat = data.get("supplier_vat")
        doc.document_number = data.get("document_number")
        doc.document_date = _parse_date(data.get("document_date"))
        doc.due_date = _parse_date(data.get("due_date"))
        doc.currency = data.get("currency")
        doc.total_ht = _parse_decimal(data.get("total_ht"))
        doc.total_tax = _parse_decimal(data.get("total_tax"))
        doc.total_ttc = _parse_decimal(data.get("total_ttc"))
        doc.extracted_data = data
        doc.ocr_status = "done"
        doc.status = "reviewed"
    except Exception as exc:
        doc.ocr_status = "failed"
        doc.ocr_error = str(exc)
        await db.commit()
        raise
    await db.commit()
    await db.refresh(doc)


@router.post("/upload", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    kind: str = Form("supplier_invoice"),
    client_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return await _store_upload(db, user, file, kind, client_id)


@router.post("/{document_id}/ocr", response_model=DocumentOut)
async def run_ocr(
    document_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    doc = await db.scalar(
        select(Document).where(Document.id == document_id, Document.tenant_id == user.tenant_id)
    )
    if not doc:
        raise HTTPException(404, "Document introuvable")
    try:
        await _process_ocr(db, doc)
    except Exception as exc:
        raise HTTPException(500, f"Erreur OCR : {exc}") from exc
    return doc


# --- Endpoints HTML form (redirigent vers /documents) ------------------------
@router.post("/upload-form")
async def upload_document_form(
    file: UploadFile = File(...),
    kind: str = Form("supplier_invoice"),
    client_id: int | None = Form(None),
    run_ocr: bool = Form(True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    doc = await _store_upload(db, user, file, kind, client_id)
    if run_ocr:
        try:
            await _process_ocr(db, doc)
        except Exception:
            # L'échec est déjà persisté (ocr_status='failed', ocr_error) ;
            # on revient à la liste qui affichera le statut + bouton "Relancer".
            pass
    return RedirectResponse(url="/documents", status_code=303)


@router.post("/{document_id}/ocr-form")
async def run_ocr_form(
    document_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    doc = await db.scalar(
        select(Document).where(Document.id == document_id, Document.tenant_id == user.tenant_id)
    )
    if not doc:
        raise HTTPException(404, "Document introuvable")
    try:
        await _process_ocr(db, doc)
    except Exception:
        pass
    return RedirectResponse(url="/documents", status_code=303)


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    doc = await db.scalar(
        select(Document).where(Document.id == document_id, Document.tenant_id == user.tenant_id)
    )
    if not doc:
        raise HTTPException(404, "Document introuvable")
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    await db.delete(doc)
    await db.commit()
    return {"deleted": True}
