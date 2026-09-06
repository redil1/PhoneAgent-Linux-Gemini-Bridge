"""Bounded action receipts and persistent duplicate protection for one call's tools."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .secure_storage import ensure_private_parent, harden_private_file

DEFAULT_JOURNAL_PATH = Path.home() / '.local/share/phone-agent/action-receipts.sqlite3'
STATES = frozenset({'requested', 'executing', 'accepted', 'completed', 'delivered', 'read', 'failed', 'unknown', 'cancelled'})
BUSINESS_RECEIPTS = {
    'business_upsert_current_lead': ('registered', 'created_or_updated', 'lead_id'),
    'business_create_opportunity': ('opportunity_created', 'created', 'opportunity_id'),
    'business_schedule_follow_up': ('follow_up_scheduled', 'scheduled', 'follow_up_id'),
    'business_create_support_ticket': ('ticket_created', 'created', 'ticket_id'),
    'business_update_support_ticket': ('updated', 'updated', 'ticket_id'),
    'business_record_call_outcome': ('outcome_recorded', 'recorded', 'call_log_id'),
    'business_mark_do_not_call': ('do_not_call_recorded', 'do_not_call', 'consent_id'),
    'business_create_quotation_draft': ('quotation_drafted', 'created', 'quotation_id'),
    'business_create_sales_order_draft': ('order_drafted', 'created', 'sales_order_id'),
}


@dataclass(frozen=True)
class ActionReceipt:
    operation_id: str
    tool: str
    channel: str
    action: str
    state: str = 'requested'
    provider_id: str = ''
    turn_epoch: int = 0
    submitted: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)

    def replay_output(self) -> str:
        return json.dumps({
            'action_receipt': self.public(), 'duplicate_prevented': True,
            'guidance': (
                'This exact request was already submitted. Report only the recorded state. '
                'Unknown or executing means the outcome needs reconciliation; do not submit it again.'
            ),
        })

    @property
    def verified_claims(self) -> frozenset[str]:
        if not self.provider_id:
            return frozenset()
        if self.channel in {'whatsapp', 'email', 'sms'}:
            if self.state == 'read':
                return frozenset({'sent', 'delivered', 'read'})
            if self.state == 'delivered':
                return frozenset({'sent', 'delivered'})
            if self.state == 'accepted':
                return frozenset({'sent'})
        elif self.state == 'completed':
            return frozenset({self.action})
        return frozenset()


def receipt_from_result(receipt: ActionReceipt, result: Any) -> ActionReceipt:
    """Use documented backend receipt fields, never a generic ok/success string."""
    if not isinstance(result, dict):
        return replace(receipt, state='unknown')
    # Managed MCP adds a trusted transport envelope. Only structured result
    # fields qualify; never parse free-form tool prose as a completion receipt.
    if result.get('ok') is True and result.get('security_notice') and isinstance(result.get('result'), dict):
        result = result['result']
    if result.get('error') or result.get('isError'):
        return replace(receipt, state='failed' if result.get('executed') is False else 'unknown')
    if isinstance(result.get('structuredContent'), dict):
        result = result['structuredContent']
    if result.get('error') or result.get('isError'):
        return replace(receipt, state='unknown')
    if result.get('channel') and result['channel'] != receipt.channel:
        return replace(receipt, state='unknown')
    if receipt.channel in {'whatsapp', 'email', 'sms'}:
        provider_id = result.get('message_id')
        if result.get('accepted') is not True or not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 256:
            return replace(receipt, state='unknown')
        state = 'accepted'
        if result.get('delivery_failed') is True:
            state = 'failed'
        elif result.get('delivery_status') == 'read' and result.get('delivery_confirmed') is True:
            state = 'read'
        elif result.get('delivery_confirmed') is True:
            state = 'delivered'
        return replace(receipt, state=state, provider_id=provider_id)
    spec = BUSINESS_RECEIPTS.get(receipt.tool)
    if spec:
        if receipt.tool.endswith('_draft') and (result.get('docstatus') != 0 or result.get('status') != 'draft_not_submitted'):
            return replace(receipt, state='unknown')
        action, flag, id_field = spec
        provider_id = result.get(id_field)
        if result.get('verified') is True and result.get(flag) is True and isinstance(provider_id, str) and provider_id.strip() and len(provider_id) <= 256:
            return replace(receipt, action=action, state='completed', provider_id=provider_id)
    return replace(receipt, state='unknown')


class ActionJournal:
    """Atomic reservation, no argument/result bodies, and restart-safe unknown outcomes.

    A reservation in executing state after restart is intentionally not retried.
    The local journal prevents duplicate dispatches; it cannot guarantee exactly-once
    execution inside a backend that lacks its own idempotency support.
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            ensure_private_parent(path)
            if path.exists():
                harden_private_file(path)
            else:
                try:
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    harden_private_file(path)
                else:
                    os.close(descriptor)
        self._db = sqlite3.connect(str(path) if path is not None else ':memory:', timeout=2, check_same_thread=False)
        self._lock = threading.RLock()
        with self._db:
            self._db.execute('CREATE TABLE IF NOT EXISTS actions (key TEXT PRIMARY KEY, receipt TEXT NOT NULL, updated REAL NOT NULL)')
            self._db.execute('DELETE FROM actions WHERE updated < ?', (time.time() - 30 * 86400,))

    @staticmethod
    def key(call_id: str, caller_id: str, tool: str, raw_arguments: str, repeat_intent: str = '') -> str:
        arguments = json.loads(raw_arguments or '{}')
        payload = json.dumps([call_id, caller_id, tool, arguments, repeat_intent], sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def reserve(self, key: str, tool: str, channel: str, action: str, epoch: int) -> tuple[ActionReceipt, bool]:
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                row = self._db.execute('SELECT receipt FROM actions WHERE key = ?', (key,)).fetchone()
                if row:
                    receipt = ActionReceipt(**json.loads(row[0]))
                    if receipt.state != 'cancelled' or receipt.submitted:
                        self._db.commit()
                        return receipt, False
                    self._db.execute('DELETE FROM actions WHERE key = ?', (key,))
                receipt = ActionReceipt('act_' + key[:32], tool, channel, action, turn_epoch=epoch)
                self._db.execute('INSERT INTO actions VALUES (?, ?, ?)', (key, json.dumps(receipt.public()), time.time()))
                self._db.commit()
                return receipt, True
            except BaseException:
                self._db.rollback()
                raise

    def save(self, key: str, receipt: ActionReceipt) -> None:
        if receipt.state not in STATES:
            raise ValueError('Unsupported action state')
        with self._lock, self._db:
            self._db.execute('UPDATE actions SET receipt = ?, updated = ? WHERE key = ?',
                             (json.dumps(receipt.public()), time.time(), key))

    def close(self) -> None:
        with self._lock:
            self._db.close()
