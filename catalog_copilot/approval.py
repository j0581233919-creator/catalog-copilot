"""Human approval workflow and persistent SQLite audit ledger for CatalogCopilot."""

from __future__ import annotations
import csv
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone, timedelta

from .models import (
    DraftMessage,
    ApprovalStatus,
    VerificationReport,
    VerificationStatus,
)
from .catalog import Catalog
from .verifier import CommercialClaimVerifier


REQUIRED_EXPORT_COLUMNS = (
    "batch_manifest_id",
    "draft_id",
    "lead_id",
    "customer_name",
    "contact_channel",
    "contact_target",
    "sku",
    "language",
    "subject",
    "body",
    "reviewed_by",
    "reviewed_at",
    "approval_status",
    "consent_to_contact",
    "consent_evidence",
)


def _is_proven_published_for_token(
    dest_path: Union[str, Path],
    batch_manifest_id: str,
    expected_drafts: Optional[List[DraftMessage]] = None,
    expected_draft_ids: Optional[List[str]] = None,
) -> bool:
    """Verifies that the file at dest_path is a valid and complete publication belonging to batch_manifest_id.
    Validates complete required schema, row-level recipient/content integrity, record count, and rejects duplicates.
    Optionally matches CSV rows directly against database DraftMessage records.
    """
    if not dest_path or not batch_manifest_id:
        return False
    p = Path(dest_path)
    if not p.is_file() or p.stat().st_size == 0:
        return False

    try:
        with open(p, "r", encoding="utf-8") as f:
            raw_reader = csv.reader(f)
            try:
                raw_headers = next(raw_reader)
            except StopIteration:
                return False
            # Reject CSV if headers do not exactly match REQUIRED_EXPORT_COLUMNS in order and count
            if tuple(raw_headers) != REQUIRED_EXPORT_COLUMNS:
                return False

            f.seek(0)
            reader = csv.DictReader(f)

            seen_draft_ids = []
            seen_draft_set = set()
            rows_by_id = {}

            for row in reader:
                # Reject malformed rows with extra columns (stored by DictReader under key None)
                if None in row:
                    return False
                # Reject rows with missing fields (values mapped to None by DictReader)
                if any(v is None for v in row.values()):
                    return False

                if row.get("batch_manifest_id") != batch_manifest_id:
                    return False

                did = (row.get("draft_id") or "").strip()
                if not did or did in seen_draft_set:
                    # Duplicate or empty draft ID: invalid publication
                    return False
                seen_draft_ids.append(did)
                seen_draft_set.add(did)

                # Validate mandatory dispatch recipient, body, status, and consent values
                channel = (row.get("contact_channel") or "").strip()
                target = (row.get("contact_target") or "").strip()
                body = (row.get("body") or "").strip()
                consent_ev = (row.get("consent_evidence") or "").strip()
                consent_opt = str(row.get("consent_to_contact") or "").strip()
                app_status = (row.get("approval_status") or "").strip()

                if not channel or not target or not body or not consent_ev:
                    return False
                if consent_opt not in ("1", "True", "true"):
                    return False
                if app_status not in (ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value):
                    return False

                rows_by_id[did] = row

            if not seen_draft_ids:
                return False

            # Validate exact unique ID set and record count against expected batch
            if expected_draft_ids is not None:
                if len(seen_draft_ids) != len(expected_draft_ids):
                    return False
                if seen_draft_set != set(expected_draft_ids):
                    return False

            # Validate exact draft contents against database draft records across all dispatch-relevant fields
            if expected_drafts is not None:
                if len(seen_draft_ids) != len(expected_drafts):
                    return False
                for exp_draft in expected_drafts:
                    r = rows_by_id.get(exp_draft.draft_id)
                    if not r:
                        return False
                    if (r.get("lead_id") or "").strip() != (exp_draft.lead_id or "").strip():
                        return False
                    if (r.get("customer_name") or "").strip() != (exp_draft.customer_name or "").strip():
                        return False
                    if (r.get("contact_channel") or "").strip() != (exp_draft.contact_channel or "").strip():
                        return False
                    if (r.get("contact_target") or "").strip() != (exp_draft.contact_target or "").strip():
                        return False
                    if (r.get("sku") or "").strip() != (exp_draft.sku or "").strip():
                        return False
                    if (r.get("language") or "").strip() != (exp_draft.language or "").strip():
                        return False
                    if (r.get("subject") or "").strip() != (exp_draft.subject or "").strip():
                        return False
                    if (r.get("body") or "").strip() != (exp_draft.body or "").strip():
                        return False
                    if (r.get("reviewed_by") or "").strip() != (exp_draft.reviewed_by or "").strip():
                        return False
                    if (r.get("reviewed_at") or "").strip() != (exp_draft.reviewed_at or "").strip():
                        return False
                    exp_status_val = exp_draft.approval_status.value if isinstance(exp_draft.approval_status, ApprovalStatus) else str(exp_draft.approval_status)
                    if (r.get("approval_status") or "").strip() != exp_status_val.strip():
                        return False
                    exp_consent = "1" if exp_draft.consent_to_contact else "0"
                    r_consent = "1" if str(r.get("consent_to_contact") or "").strip() in ("1", "True", "true") else "0"
                    if r_consent != exp_consent:
                        return False
                    if (r.get("consent_evidence") or "").strip() != (exp_draft.consent_evidence or "").strip():
                        return False

            return True
    except Exception:
        return False


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
            if "reservation_token" not in existing_columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN reservation_token TEXT")
            if "reservation_expires_at" not in existing_columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN reservation_expires_at TEXT")
            if "reservation_destination" not in existing_columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN reservation_destination TEXT")

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
                  AND reservation_token IS NULL
                ORDER BY reviewed_at ASC
                """,
                (ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value),
            )
            return [self._row_to_draft(row) for row in cursor.fetchall()]

    def reserve_for_export(
        self,
        batch_manifest_id: str,
        export_destination: str,
        lease_seconds: int = 300,
    ) -> List[DraftMessage]:
        """Atomically reserves approved, unexported drafts under a lease token bound to an export destination.
        Protects against concurrent export workers and prevents duplicate exports on crash.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        lease_expiry_iso = (now + timedelta(seconds=lease_seconds)).isoformat()
        dest_str = str(Path(export_destination).resolve())

        with self._get_connection() as conn:
            # 1. Reconcile expired reservations before taking new ones
            cursor = conn.execute(
                """
                SELECT DISTINCT reservation_token, reservation_destination
                FROM drafts
                WHERE reservation_token IS NOT NULL
                  AND reservation_expires_at < ?
                  AND approval_status IN (?, ?)
                """,
                (now_iso, ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value),
            )
            expired_tokens = cursor.fetchall()
            for exp in expired_tokens:
                r_token = exp["reservation_token"]
                r_dest = exp["reservation_destination"]
                t_cursor = conn.execute(
                    """
                    SELECT * FROM drafts
                    WHERE reservation_token = ?
                      AND approval_status IN (?, ?)
                    """,
                    (r_token, ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value),
                )
                t_rows = t_cursor.fetchall()
                batch_ids = [tr["draft_id"] for tr in t_rows]
                batch_drafts = [self._row_to_draft(tr) for tr in t_rows]

                # If destination file was proven published for this token and all drafts, finalize it!
                if r_dest and _is_proven_published_for_token(r_dest, r_token, expected_drafts=batch_drafts, expected_draft_ids=batch_ids):
                    for tr in t_rows:
                        d_id = tr["draft_id"]
                        actual_prev = tr["approval_status"]
                        conn.execute(
                            """
                            UPDATE drafts
                            SET approval_status = ?, is_dispatched = 0,
                                reservation_token = NULL, reservation_destination = NULL, reservation_expires_at = NULL
                            WHERE draft_id = ? AND reservation_token = ?
                            """,
                            (ApprovalStatus.EXPORTED.value, d_id, r_token),
                        )
                        self._log_audit_event(
                            conn=conn,
                            draft_id=d_id,
                            action="EXPORTED",
                            actor="System/LeaseReconciliation",
                            previous_status=actual_prev,
                            new_status="EXPORTED",
                            details={"reconciled_destination": r_dest, "batch_manifest_id": r_token},
                        )
                elif not r_dest or not os.path.exists(r_dest):
                    # File was not created: safe to release back to queue
                    conn.execute(
                        """
                        UPDATE drafts
                        SET reservation_token = NULL, reservation_destination = NULL, reservation_expires_at = NULL
                        WHERE reservation_token = ?
                        """,
                        (r_token,),
                    )
                else:
                    # Destination file exists but is partial/ambiguous: preserve reservation for administrator intervention
                    pass

            # 2. Select eligible drafts for reservation
            cursor = conn.execute(
                """
                SELECT * FROM drafts
                WHERE approval_status IN (?, ?)
                  AND is_dispatched = 0
                  AND consent_to_contact = 1
                  AND verification_status = 'PASS'
                  AND reservation_token IS NULL
                ORDER BY reviewed_at ASC
                """,
                (ApprovalStatus.APPROVED.value, ApprovalStatus.EDITED_AND_APPROVED.value),
            )
            rows = cursor.fetchall()
            if not rows:
                return []

            draft_ids = [row["draft_id"] for row in rows]
            placeholders = ",".join("?" for _ in draft_ids)
            conn.execute(
                f"""
                UPDATE drafts
                SET reservation_token = ?, reservation_destination = ?, reservation_expires_at = ?
                WHERE draft_id IN ({placeholders})
                """,
                [batch_manifest_id, dest_str, lease_expiry_iso] + draft_ids,
            )
            conn.commit()

            return [self._row_to_draft(row) for row in rows]

    def release_reservation(self, batch_manifest_id: str) -> int:
        """Releases an active reservation token back to the queue ONLY if the export file was not published.
        If the export file was actually published on disk, it is finalized instead to prevent duplicates.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM drafts
                WHERE reservation_token = ?
                """,
                (batch_manifest_id,),
            )
            rows = cursor.fetchall()
            if not rows:
                return 0

            draft_ids = [r["draft_id"] for r in rows]
            draft_objs = [self._row_to_draft(r) for r in rows]
            sample_dest = rows[0]["reservation_destination"]

            # Check if destination was proven published on disk for this exact token and draft list
            if sample_dest and _is_proven_published_for_token(sample_dest, batch_manifest_id, expected_drafts=draft_objs, expected_draft_ids=draft_ids):
                # File was published! Do NOT release back to queue! Finalize as EXPORTED!
                conn.close()
                return self.mark_exported(draft_ids=draft_ids, batch_manifest_id=batch_manifest_id)

            # Destination does not exist: safe to release back to queue
            if not sample_dest or not os.path.exists(sample_dest):
                cursor = conn.execute(
                    """
                    UPDATE drafts
                    SET reservation_token = NULL, reservation_destination = NULL, reservation_expires_at = NULL
                    WHERE reservation_token = ?
                    """,
                    (batch_manifest_id,),
                )
                count = cursor.rowcount
                conn.commit()
                return count

            # Destination file exists but is partial or ambiguous: preserve for administrator intervention
            return 0

    def mark_exported(self, draft_ids: List[str], batch_manifest_id: str) -> int:
        """Atomically transitions reserved drafts to EXPORTED and clears reservation tokens.
        Enforces strict token ownership: cannot finalize unreserved drafts or another worker's reservation.
        Accurately captures prior approval status (APPROVED vs EDITED_AND_APPROVED) in the audit log.
        """
        if not draft_ids or not batch_manifest_id:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        updated_count = 0

        with self._get_connection() as conn:
            for draft_id in draft_ids:
                # Capture actual prior status bound to the active reservation token
                cursor = conn.execute(
                    """
                    SELECT approval_status FROM drafts
                    WHERE draft_id = ?
                      AND reservation_token = ?
                      AND approval_status IN (?, ?)
                    """,
                    (
                        draft_id,
                        batch_manifest_id,
                        ApprovalStatus.APPROVED.value,
                        ApprovalStatus.EDITED_AND_APPROVED.value,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    continue

                actual_prior_status = row["approval_status"]

                # Atomic transition requiring exact draft_id, token, and prior status
                cursor = conn.execute(
                    """
                    UPDATE drafts
                    SET approval_status = ?, is_dispatched = 0,
                        reservation_token = NULL, reservation_destination = NULL, reservation_expires_at = NULL
                    WHERE draft_id = ?
                      AND reservation_token = ?
                      AND approval_status = ?
                    """,
                    (
                        ApprovalStatus.EXPORTED.value,
                        draft_id,
                        batch_manifest_id,
                        actual_prior_status,
                    ),
                )
                if cursor.rowcount > 0:
                    self._log_audit_event(
                        conn=conn,
                        draft_id=draft_id,
                        action="EXPORTED",
                        actor="System/DispatchExporter",
                        previous_status=actual_prior_status,
                        new_status="EXPORTED",
                        details={"batch_manifest_id": batch_manifest_id},
                    )
                    updated_count += 1
            conn.commit()

        return updated_count

    def recover_interrupted_exports(self, export_dir: Union[str, Path]) -> Dict[str, Any]:
        """Scans export directory and reconciles reservations scoped to that directory.
        Never releases live active reservations or reservations belonging to other directories.
        """
        export_path = Path(export_dir).resolve()
        now_iso = datetime.now(timezone.utc).isoformat()
        recovered = []
        released = []

        if not export_path.exists():
            return {"recovered": recovered, "released": released}

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM drafts
                WHERE reservation_token IS NOT NULL
                """
            )
            rows = cursor.fetchall()

        # Group by reservation_token
        tokens_map: Dict[str, list] = {}
        for r in rows:
            tok = r["reservation_token"]
            if tok not in tokens_map:
                tokens_map[tok] = []
            tokens_map[tok].append(r)

        for token, token_rows in tokens_map.items():
            dest_str = token_rows[0]["reservation_destination"]
            if not dest_str:
                continue

            dest_p = Path(dest_str).resolve()
            # STRICT DIRECTORY SCOPING: Only touch reservations scoped inside export_path!
            try:
                dest_p.relative_to(export_path)
            except ValueError:
                # Reservation is for a different directory: leave completely untouched!
                continue

            expires_at = token_rows[0]["reservation_expires_at"]
            draft_ids = [r["draft_id"] for r in token_rows]
            draft_objs = [self._row_to_draft(r) for r in token_rows]

            # Case A: Export CSV was proven published on disk for this token and complete draft batch!
            if dest_p.is_file() and _is_proven_published_for_token(dest_p, token, expected_drafts=draft_objs, expected_draft_ids=draft_ids):
                # Reconcile publication: mark exported and ensure manifest matches published CSV
                manifest_p = dest_p.with_suffix(".manifest.json")
                manifest_data = {
                    "batch_manifest_id": token,
                    "export_file": dest_p.name,
                    "record_count": len(draft_ids),
                    "draft_ids": draft_ids,
                    "exported_at": now_iso,
                    "reconciled_by": "System/CrashRecovery",
                }
                try:
                    with open(manifest_p, "w", encoding="utf-8") as mf:
                        json.dump(manifest_data, mf, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                self.mark_exported(draft_ids=draft_ids, batch_manifest_id=token)
                recovered.append(token)

            # Case B: Export CSV does NOT exist on disk:
            elif not dest_p.exists():
                # Only release if lease is expired! If lease is still live, another worker is actively writing!
                if expires_at and expires_at < now_iso:
                    self.release_reservation(token)
                    released.append(token)
                else:
                    # Still live active lease: do not disrupt!
                    pass

            # Case C: Destination file exists on disk but is partial or ambiguous (e.g. truncated write):
            else:
                # Preserve reservation for administrator intervention; do NOT finalize and do NOT release!
                pass

        return {"recovered": recovered, "released": released}

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

    def get_audit_history(self, draft_id: str) -> List[Dict[str, Any]]:
        """Returns the chronological audit log entries for a draft."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT log_id, draft_id, action, actor, timestamp, previous_status, new_status, details_json
                FROM audit_log
                WHERE draft_id = ?
                ORDER BY timestamp ASC, log_id ASC
                """,
                (draft_id,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "log_id": r["log_id"],
                    "draft_id": r["draft_id"],
                    "action": r["action"],
                    "actor": r["actor"],
                    "timestamp": r["timestamp"],
                    "previous_status": r["previous_status"],
                    "new_status": r["new_status"],
                    "details": json.loads(r["details_json"]) if r["details_json"] else {},
                }
                for r in rows
            ]

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
