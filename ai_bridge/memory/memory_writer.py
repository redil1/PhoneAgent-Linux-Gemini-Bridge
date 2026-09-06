"""Validated Memory Writer for PhoneAgent.

Asynchronously analyzes completed speech turns, extracts verified facts/preferences,
and writes them to long-term caller memory without blocking real-time voice latency.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..feature_flags import feature_flag_enabled
from ..identity.memory import AsyncIdentityMemory, LocalEpisodeStore, scope_hash
from ..identity.models import MemoryBlock, MemorySource
from ..identity.store import DEFAULT_IDENTITY_ROOT, IdentityStore
from .memory_manager import LayeredMemoryManager

logger = logging.getLogger(__name__)


class ValidatedMemoryWriter:
    """Extracts verified preferences and lessons from turns and commits them to memory."""

    def __init__(
        self,
        memory_manager: LayeredMemoryManager | None = None,
        identity_memory: AsyncIdentityMemory | None = None,
    ) -> None:
        self.memory_manager = memory_manager or LayeredMemoryManager()
        self._identity_memory = identity_memory
        self._owns_identity_memory = identity_memory is None
        self._closed = False
        self._memory_lock = threading.Lock()
        self._identity_proposals_enabled = feature_flag_enabled(
            "PHONE_AGENT_IDENTITY_PROPOSALS_ENABLED", default=False
        )

    def _long_term_memory(self) -> AsyncIdentityMemory:
        with self._memory_lock:
            if self._closed or not self.memory_manager.persistence_enabled:
                raise RuntimeError("Caller memory writer is closed or disabled")
            if self._identity_memory is not None:
                return self._identity_memory
        configured = os.getenv("PHONE_AGENT_IDENTITY_MEMORY_DB", "").strip()
        path = (Path(configured).expanduser() if configured
                else self.memory_manager.storage_path.with_name("identity-memory.sqlite3"))
        # Database initialization can block. Never hold the lifecycle lock
        # across I/O, and do not revive a worker after close has already run.
        candidate = AsyncIdentityMemory(LocalEpisodeStore(path), namespace=self.memory_manager.namespace)
        with self._memory_lock:
            if not self._closed and self._identity_memory is None:
                self._identity_memory = candidate
            selected = self._identity_memory if not self._closed else None
        if selected is not candidate:
            candidate.close(timeout=0)
        if selected is None:
            raise RuntimeError("Caller memory writer closed during initialization")
        return selected

    async def close(self) -> None:
        """Close an owned episode worker without creating one during shutdown."""
        with self._memory_lock:
            self._closed = True
            memory = self._identity_memory
        if memory is None or not self._owns_identity_memory or memory.close(timeout=0):
            return
        try:
            drained = await asyncio.wait_for(asyncio.to_thread(memory.close, 0.5), timeout=0.75)
        except TimeoutError:
            drained = False
        if not drained:
            logger.info("Caller memory worker is still draining accepted episodes in the background")

    async def process_turn_async(
        self,
        phone_number: str,
        caller_text: str,
        ai_response: str,
        turn_latency_ms: float = 0.0,
        fidelity_score: float = 100.0,
        task_id: str = "",
        evaluation_feedback: list[str] | None = None,
        *, delivery_status: str = "unverified",
    ) -> None:
        """Process one turn off the real-time event loop."""

        if self._closed or not self.memory_manager.persistence_enabled:
            return
        await asyncio.to_thread(
            self._process_turn,
            phone_number,
            caller_text,
            ai_response,
            turn_latency_ms,
            fidelity_score,
            task_id,
            evaluation_feedback or [],
            delivery_status,
        )

    def _process_turn(
        self,
        phone_number: str,
        caller_text: str,
        ai_response: str,
        turn_latency_ms: float,
        fidelity_score: float,
        task_id: str,
        evaluation_feedback: list[str],
        delivery_status: str = "unverified",
    ) -> None:
        if self._closed or not self.memory_manager.persistence_enabled:
            return
        try:
            if re.search(r"[\u0600-\u06ff]", caller_text + ai_response):
                logger.info(
                    "Skipped memory write for %s because the turn is outside the "
                    "English/French policy",
                    phone_number,
                )
                return

            # Only acknowledged complete playback enters remembered agent speech.
            # Keep generated text separately for diagnostics; partial word-level
            # delivery cannot be reconstructed from a count of audio frames.
            remembered_ai = ai_response if delivery_status == "completed" else ""
            # 1. Record episodic turn and its explicit delivery evidence
            self.memory_manager.record_turn(
                phone_number,
                caller_text=caller_text,
                ai_response=remembered_ai,
                generated_ai_response=ai_response,
                delivery_status=delivery_status,
                turn_latency_ms=turn_latency_ms,
                fidelity_score=fidelity_score,
                task_id=task_id,
                evaluation_feedback=evaluation_feedback,
            )
            memory = self._long_term_memory()
            call_reference = f"{task_id}:{int(time.time())}"
            language = "fr" if re.search(r"[àâçéèêëîïôùûüÿœ]", caller_text.lower()) else "en"
            memory.submit_turn(
                caller_id=phone_number,
                call_id=call_reference,
                role="caller",
                content=caller_text,
                language=language,
                task_id=task_id,
            )
            if remembered_ai:
                memory.submit_turn(
                    caller_id=phone_number,
                    call_id=call_reference,
                    role="agent",
                    content=remembered_ai,
                    language=language,
                    task_id=task_id,
                )

            # 2. Extract explicit language preferences
            extracted_prefs: dict[str, Any] = {}
            if re.search(r"\b(français|french)\b", caller_text, re.IGNORECASE):
                extracted_prefs["preferred_language"] = "fr-FR"
            elif re.search(r"\b(english|anglais)\b", caller_text, re.IGNORECASE):
                extracted_prefs["preferred_language"] = "en-US"

            # 3. Extract a caller name only when it is explicitly stated.
            name_match = re.search(
                r"(?:je m'appelle|my name is)\s+([A-Za-zÀ-ÖØ-öø-ÿ'-]{2,30})",
                caller_text,
                re.IGNORECASE,
            )
            if name_match:
                self.memory_manager.update_identity(
                    phone_number,
                    name=name_match.group(1).strip(),
                )

            if extracted_prefs:
                self.memory_manager.update_preferences(phone_number, extracted_prefs)
                logger.info(
                    "Committed validated preferences for %s: %s", phone_number, extracted_prefs
                )
                if self._identity_proposals_enabled:
                    root = Path(
                        os.getenv("PHONE_AGENT_IDENTITY_ROOT", "").strip() or DEFAULT_IDENTITY_ROOT
                    ).expanduser()
                    language = str(extracted_prefs["preferred_language"])
                    proposal = IdentityStore(root).create_memory_proposal(
                        MemoryBlock(
                            block_id=f"caller_language_{scope_hash(phone_number)[7:23]}",
                            kind="human",
                            label="Caller language preference",
                            content=f"The caller explicitly prefers {language}.",
                            mutable=True,
                            priority=75,
                            source=MemorySource.AGENT_INFERRED,
                            confidence=1.0,
                            caller_scope_hash=scope_hash(phone_number),
                        ),
                        evidence=f"Explicit caller statement: {caller_text[:500]}",
                    )
                    logger.info(
                        "identity_proposal_created proposal_id=%s scope=%s",
                        proposal.proposal_id,
                        proposal.block.caller_scope_hash,
                    )

        except Exception as exc:
            logger.error("Error in background memory writer: %s", exc)
