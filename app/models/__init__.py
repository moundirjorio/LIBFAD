"""Modèles SQLAlchemy — importés ici pour qu'Alembic les détecte."""

from app.models.audit import AuditLog
from app.models.bank import BankAccount, BankTransaction
from app.models.chat import ChatMessage, ChatThread
from app.models.client import Client, Contact
from app.models.client_portal import ClientPortalUser
from app.models.contract import Contract
from app.models.document import Document
from app.models.dossier import DossierAccess
from app.models.dunning import (
    DunningMessage,
    DunningRun,
    DunningScenario,
    DunningStep,
)
from app.models.email_template import EmailTemplate
from app.models.invoice import Invoice, InvoiceItem
from app.models.ledger import Journal, JournalEntry, JournalEntryLine, LedgerAccount
from app.models.notification import Notification, Task
from app.models.numbering import NumberingSequence
from app.models.password_reset import PasswordResetToken
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase_receipt import PurchaseReceipt
from app.models.quote import Quote, QuoteItem
from app.models.subscription import Subscription
from app.models.supplier import Supplier
from app.models.tax_rate import TaxRate
from app.models.telegram import TelegramLink
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
    "AuditLog",
    "BankAccount",
    "BankTransaction",
    "ChatMessage",
    "ChatThread",
    "Client",
    "ClientPortalUser",
    "Contact",
    "Contract",
    "Document",
    "DossierAccess",
    "DunningMessage",
    "DunningRun",
    "DunningScenario",
    "DunningStep",
    "EmailTemplate",
    "Invoice",
    "InvoiceItem",
    "Journal",
    "JournalEntry",
    "JournalEntryLine",
    "LedgerAccount",
    "Notification",
    "NumberingSequence",
    "PasswordResetToken",
    "Payment",
    "Product",
    "PurchaseReceipt",
    "Quote",
    "QuoteItem",
    "Subscription",
    "Supplier",
    "Task",
    "TaxRate",
    "TelegramLink",
    "Tenant",
    "User",
]
