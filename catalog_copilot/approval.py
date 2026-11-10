"""Human approval workflow and persistent SQLite audit ledger for CatalogCopilot."""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone

from .models import (
    DraftMessage,
    ApprovalStatus,
    VerificationReport,
    VerificationStatus,
)
from .catalog import Catalog
from .verifier import CommercialClaimVerifier


class ApprovalLedger:
    """Manages drafts, human review workflows, and immutable audit logs in SQLite."""

    def __init__(self, db_path: Union[str, Path] = "catalog_copilot.db"):
        self.db_path = str(db_path)
        self.verifier = CommercialClaimVerifier()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes tables, migrations, and immutability triggers for audit logging."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    customer_name TEXT,
                    contact_channel TEXT NOT NULL,
                    contact_target TEXT NOT NULL,
                    sku TEXT,
                    language TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    consent_to_contact INTEGER NOT NULL DEFAULT 0,
                    consent_evidence TEXT,
                    verification_status TEXT NOT NULL,
                    verification_report_json TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    reviewer_notes TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    is_dispatched INTEGER DEFAULT 0,
                    dispatched_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Schema migration check: ensure legacy databases acquire consent columns safely
            cursor = conn.execute("PRAGMA table_info(drafts)")
            existing_columns = {row["name"] for row in cursor.fetchall()}
            if "consent_to_contact" not in existing_columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN consent_to_contact INTEGER NOT NULL DEFAULT 0")
            if "consent_evidence" not in existing_columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN consent_evidence TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    details_json TEXT,
                    timestamp TEXT NOT NULL
                )
            """)

            # Immutability triggers for audit_log: prohibit application-level UPDATE or DELETE
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trig_audit_log_immutable_update
                BEFORE UPDATE ON audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'Security Invariant: audit_log records are immutable and cannot be updated.');
                END;
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trig_audit_log_immutable_delete
                BEFORE DELETE ON audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'Security Invariant: audit_log records are immutable and cannot be deleted.');
                END;
            """)
            conn.commit()

    def _log_audit_event(
        self,
        conn: sqlite3.Connection,
        draft_id: str,
        action: str,
        actor: str,
        previous_status: Optional[str],
        new_status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO audit_log (
                draft_id, action, actor, previous_status, new_status, details_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                action,
                actor,
                previous_status,
                new_status,
                json.dumps(details or {}, ensure_ascii=False),
                now,
            ),
        )

    def save_draft(self, draft: DraftMessage) -> None:
        """Stores a draft. Invariant: ALL drafts strictly enter in PENDING_REVIEW state.
        No caller can insert a pre-approved or pre-dispatched draft directly.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT approval_status FROM drafts WHERE draft_id = ?", (draft.draft_id,))
            row = cursor.fetchone()
            if row:
                existing_status = row["approval_status"]
                if existing_status != ApprovalStatus.PENDING_REVIEW.value:
                    raise ValueError(
                        f"Cannot overwrite draft '{draft.draft_id}': already finalized in status '{existing_status}'."
                    )

            # Strict invariant: newly saved drafts ALWAYS start in PENDING_REVIEW
            enforced_status = ApprovalStatus.PENDING_REVIEW.value

            conn.execute(
                """
                INSERT OR REPLACE INTO drafts (
                    draft_id, lead_id, customer_name, contact_channel, contact_target,
                    sku, language, subject, body, consent_to_contact, consent_evidence,
                    verification_status, verification_report_json, approval_status,
                    reviewer_notes, reviewed_by, reviewed_at, is_dispatched, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.lead_id,
                    draft.customer_name,
                    draft.contact_channel,
                    draft.contact_target,
                    draft.sku,
                    draft.language,
                    draft.subject,
                    draft.body,
                    1 if draft.consent_to_contact else 0,
                    draft.consent_evidence,
                    draft.verification_report.status.value,
                    json.dumps(draft.verification_report.to_dict(), ensure_ascii=False),
                    enforced_status,
                    None,
                    None,
                    None,
                    0,
                    draft.created_at,
                ),
            )
            self._log_audit_event(
                conn=conn,
                draft_id=draft.draft_id,
                action="DRAFT_CREATED",
                actor="System/DraftGenerator",
                previous_status=None,
                new_status=enforced_status,
                details={
                    "verification_status": draft.verification_report.status.value,
                    "consent_to_contact": draft.consent_to_contact,
                    "consent_evidence": draft.consent_evidence,
                    "reasons": draft.verification_report.reasons,
                },
            )
            conn.commit()

    def get_draft(self, draft_id: str) -> Optional[DraftMessage]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_draft(row)

    def list_drafts(self, status: Optional[ApprovalStatus] = None) -> List[DraftMessage]:
        with self._get_connection() as conn:
            if status is not None:
                cursor = conn.execute("SELECT * FROM drafts WHERE approval_status = ? ORDER BY created_at DESC", (status.value,))
            else:
                cursor = conn.execute("SELECT * FROM drafts ORDER BY created_at DESC")
            return [self._row_to_draft(row) for row in cursor.fetchall()]

    def approve(self, draft_id: str, reviewer: str, notes: Optional[str] = None) -> DraftMessage:
        """Approves a draft for outbound dispatch. Enforces predecessor state and full verification PASS."""
        reviewer = str(reviewer or "").strip()
        if not reviewer:
            raise ValueError("Reviewer identity is required to approve drafts.")

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,))
            row = cursor.fetchone()
            if not row:
                raise KeyError(f"Draft '{draft_id}' not found.")

            draft = self._row_to_draft(row)

            # Predecessor state check: only PENDING_REVIEW can be approved
            if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
                raise ValueError(
                    f"Cannot approve draft '{draft_id}': current status is '{draft.approval_status.value}', must be '{ApprovalStatus.PENDING_REVIEW.value}'."
                )

            # Strict consent guardrail
            if not draft.consent_to_contact:
                raise ValueError(
                    f"Cannot approve draft '{draft_id}': Customer has not granted opt-in contact consent. Outreach strictly prohibited."
                )

            # Strict verification guardrail: MUST be PASS (neither FAIL nor UNCERTAIN allowed)
            if draft.verification_report.status != VerificationStatus.PASS:
                raise ValueError(
                    f"Cannot approve draft '{draft_id}': Verification status is '{draft.verification_report.status.value}'. "
                    f"Only verified PASS drafts may be approved. Reasons: {'; '.join(draft.verification_report.reasons)}"
                )

            now = datetime.now(timezone.utc).isoformat()
            prev_status = draft.approval_status.value
            new_status = ApprovalStatus.APPROVED.value

            conn.execute(
                """
                UPDATE drafts
                SET approval_status = ?, reviewed_by = ?, reviewer_notes = ?, reviewed_at = ?
                WHERE draft_id = ?
                """,
                (new_status, reviewer, notes, now, draft_id),
            )
            self._log_audit_event(
                conn=conn,
                draft_id=draft_id,
                action="APPROVE",
                actor=reviewer,
                previous_status=prev_status,
                new_status=new_status,
                details={"notes": notes, "verification_status": draft.verification_report.status.value},
            )
            conn.commit()

            draft.approval_status = ApprovalStatus.APPROVED
            draft.reviewed_by = reviewer
            draft.reviewer_notes = notes
            draft.reviewed_at = now
            return draft

    def reject(self, draft_id: str, reviewer: str, reason: str) -> DraftMessage:
        """Rejects a draft with an explicit audit reason."""
        reviewer = str(reviewer or "").strip()
        reason = str(reason or "").strip()
        if not reviewer:
            raise ValueError("Reviewer identity is required to reject drafts.")
        if not reason:
            raise ValueError("Rejection reason must be provided for audit tracking.")

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,))
            row = cursor.fetchone()
            if not row:
                raise KeyError(f"Draft '{draft_id}' not found.")

            draft = self._row_to_draft(row)

            if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
                raise ValueError(
                    f"Cannot reject draft '{draft_id}': current status is '{draft.approval_status.value}', must be '{ApprovalStatus.PENDING_REVIEW.value}'."
                )

            prev_status = draft.approval_status.value
            new_status = ApprovalStatus.REJECTED.value
            now = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """
                UPDATE drafts
                SET approval_status = ?, reviewed_by = ?, reviewer_notes = ?, reviewed_at = ?
                WHERE draft_id = ?
                """,
                (new_status, reviewer, reason, now, draft_id),
            )
            self._log_audit_event(
                conn=conn,
                draft_id=draft_id,
                action="REJECT",
                actor=reviewer,
                previous_status=prev_status,
                new_status=new_status,
                details={"reason": reason},
            )
            conn.commit()

            draft.approval_status = ApprovalStatus.REJECTED
            draft.reviewed_by = reviewer
            draft.reviewer_notes = reason
            draft.reviewed_at = now
            return draft

    def edit_and_approve(
        self,
        draft_id: str,
        new_body: str,
        reviewer: str,
        catalog: Catalog,
        notes: Optional[str] = None,
    ) -> DraftMessage:
        """Applies manual human edits and verifies them deterministically.
        Strictly preserves the original customer consent status.
        """
        reviewer = str(reviewer or "").strip()
        new_body = str(new_body or "").strip()
        if not reviewer:
            raise ValueError("Reviewer identity is required.")
        if not new_body:
            raise ValueError("New body cannot be empty.")

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,))
            row = cursor.fetchone()
            if not row:
                raise KeyError(f"Draft '{draft_id}' not found.")

            draft = self._row_to_draft(row)

            if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
                raise ValueError(
                    f"Cannot edit and approve draft '{draft_id}': status is '{draft.approval_status.value}', must be '{ApprovalStatus.PENDING_REVIEW.value}'."
                )

            if not draft.consent_to_contact:
                raise ValueError(
                    f"Cannot edit and approve draft '{draft_id}': Customer has not granted opt-in contact consent. Outreach strictly prohibited."
                )

            from .models import LeadInquiry
            lead = LeadInquiry(
                lead_id=draft.lead_id,
                customer_name=draft.customer_name,
                contact_channel=draft.contact_channel,
                contact_target=draft.contact_target,
                consent_to_contact=draft.consent_to_contact,
                preferred_language=draft.language,
                inquiry_text="",
                requested_sku=draft.sku,
                consent_evidence=draft.consent_evidence,
            )

            new_report = self.verifier.verify(
                lead=lead,
                catalog=catalog,
                draft_text=new_body,
                target_sku=draft.sku,
            )

            if new_report.status != VerificationStatus.PASS:
                raise ValueError(
                    f"Edited message failed deterministic verification ({new_report.status.value}): {'; '.join(new_report.reasons)}"
                )

            now = datetime.now(timezone.utc).isoformat()
            prev_status = draft.approval_status.value
            new_status = ApprovalStatus.EDITED_AND_APPROVED.value

            conn.execute(
                """
                UPDATE drafts
                SET body = ?, verification_status = ?, verification_report_json = ?,
                    approval_status = ?, reviewed_by = ?, reviewer_notes = ?, reviewed_at = ?
                WHERE draft_id = ?
                """,
                (
                    new_body,
                    new_report.status.value,
                    json.dumps(new_report.to_dict(), ensure_ascii=False),
                    new_status,
                    reviewer,
                    notes or "Edited and approved by human reviewer",
                    now,
                    draft_id,
                ),
            )
            self._log_audit_event(
                conn=conn,
                draft_id=draft_id,
                action="EDIT_AND_APPROVE",
                actor=reviewer,
                previous_status=prev_status,
                new_status=new_status,
                details={
                    "original_body": draft.body,
                    "new_body": new_body,
                    "notes": notes,
                    "verification_status": new_report.status.value,
                },
            )
            conn.commit()

            draft.body = new_body
            draft.verification_report = new_report
            draft.approval_status = ApprovalStatus.EDITED_AND_APPROVED
            draft.reviewed_by = reviewer
            draft.reviewer_notes = notes
            draft.reviewed_at = now
            return draft

    def get_approved_for_dispatch(self) -> List[DraftMessage]:
        """Returns only drafts that are strictly approved, consented, verified PASS, and unexported."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM drafts
                WHERE approval_status IN (?, ?)
                  AND is_dispatched = 0
                  AND consent_to_contact = 1
                  AND verification_status = 'PASS'
                ORDER BY reviewed_at ASC
                """,
                (ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value),
            )
            return [self._row_to_draft(row) for row in cursor.fetchall()]

    def mark_exported(self, draft_ids: List[str], batch_manifest_id: str) -> int:
        """Atomically transitions approved drafts to EXPORTED in a single transaction.
        Returns the exact number of rows successfully updated.
        """
        if not draft_ids:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        updated_count = 0

        with self._get_connection() as conn:
            for draft_id in draft_ids:
                cursor = conn.execute("SELECT approval_status FROM drafts WHERE draft_id = ?", (draft_id,))
                row = cursor.fetchone()
                if not row:
                    continue

                prev_status = row["approval_status"]
                if prev_status not in (ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value):
                    continue

                conn.execute(
                    """
                    UPDATE drafts
                    SET approval_status = ?, is_dispatched = 0
                    WHERE draft_id = ?
                    """,
                    (ApprovalStatus.EXPORTED.value, draft_id),
                )
                self._log_audit_event(
                    conn=conn,
                    draft_id=draft_id,
                    action="EXPORTED",
                    actor="System/DispatchExporter",
                    previous_status=prev_status,
                    new_status="EXPORTED",
                    details={"batch_manifest_id": batch_manifest_id},
                )
                updated_count += 1
            conn.commit()

        return updated_count

    def mark_dispatched(self, draft_id: str) -> None:
        """Marks an exported or approved draft as dispatched upon delivery confirmation."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT approval_status FROM drafts WHERE draft_id = ?", (draft_id,))
            row = cursor.fetchone()
            if not row:
                raise KeyError(f"Draft '{draft_id}' not found for dispatch confirmation.")

            prev_status = row["approval_status"]
            if prev_status not in (
                ApprovalStatus.EXPORTED.value,
                ApprovalStatus.APPROVED.value,
                ApprovalStatus.EDITED_AND_APPROVED.value,
            ):
                raise ValueError(
                    f"Cannot mark draft '{draft_id}' as dispatched: invalid predecessor status '{prev_status}'."
                )

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE drafts
                SET is_dispatched = 1, dispatched_at = ?, approval_status = ?
                WHERE draft_id = ?
                """,
                (now, ApprovalStatus.DISPATCHED.value, draft_id),
            )
            self._log_audit_event(
                conn=conn,
                draft_id=draft_id,
                action="DISPATCHED",
                actor="System/DeliveryWorker",
                previous_status=prev_status,
                new_status="DISPATCHED",
            )
            conn.commit()

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> DraftMessage:
        report_data = json.loads(row["verification_report_json"])
        report = VerificationReport.from_dict(report_data)
        return DraftMessage(
            draft_id=row["draft_id"],
            lead_id=row["lead_id"],
            customer_name=row["customer_name"] or "",
            contact_channel=row["contact_channel"] or "email",
            contact_target=row["contact_target"] or "",
            sku=row["sku"],
            language=row["language"] or "ar",
            subject=row["subject"] or "",
            body=row["body"] or "",
            verification_report=report,
            consent_to_contact=bool(row["consent_to_contact"]),
            consent_evidence=row["consent_evidence"] or "",
            approval_status=ApprovalStatus(row["approval_status"]),
            reviewer_notes=row["reviewer_notes"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
            created_at=row["created_at"],
        )
