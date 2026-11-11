"""Cryptographic audit export, receipt generation, and verification engine for CatalogCopilot.

Provides:
- Tamper-evident export packaging with verifiable SHA-256 digests.
- Cumulative record hash chaining linking approved messages to SQLite audit logs.
- Accompanied cryptographic receipts (.receipt.json) certifying export integrity.
- Verification routine validating file bytes, records, and database audit events.
- Strict and honest compliance framing: proves local bit-level integrity and immutable
  ledger alignment without fabricating sovereign certificates or third-party endorsements.
"""

from __future__ import annotations
import csv
import json
import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple

from .models import DraftMessage, ApprovalStatus, VerificationStatus
from .approval import ApprovalLedger
from .csv_security import sanitize_csv_row, desanitize_csv_row

AUDIT_RECEIPT_VERSION = "1.0.0"
COMPLIANCE_DISCLAIMER = (
    "This cryptographic audit receipt verifies bit-level file integrity (SHA-256) and "
    "mathematical alignment with CatalogCopilot's immutable SQLite audit ledger. "
    "It provides tamper-evident auditability but does not constitute a third-party legal "
    "certification or external digital signature authority."
)


@dataclass
class AuditReceipt:
    """Cryptographic audit receipt certifying export provenance and integrity."""
    batch_id: str
    exported_at: str
    export_file: str
    format: str
    record_count: int
    draft_ids: List[str]
    file_sha256: str
    records_sha256_chain: str
    audit_events_count: int
    audit_events_sha256: str
    exported_by: str
    format_version: str = AUDIT_RECEIPT_VERSION
    compliance_statement: str = COMPLIANCE_DISCLAIMER

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format_version": self.format_version,
            "batch_id": self.batch_id,
            "exported_at": self.exported_at,
            "export_file": self.export_file,
            "format": self.format,
            "record_count": self.record_count,
            "draft_ids": self.draft_ids,
            "file_sha256": self.file_sha256,
            "records_sha256_chain": self.records_sha256_chain,
            "audit_events_count": self.audit_events_count,
            "audit_events_sha256": self.audit_events_sha256,
            "exported_by": self.exported_by,
            "compliance_statement": self.compliance_statement,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: Union[str, Path]) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuditReceipt:
        return cls(
            batch_id=data["batch_id"],
            exported_at=data["exported_at"],
            export_file=data["export_file"],
            format=data.get("format", "csv"),
            record_count=data["record_count"],
            draft_ids=data.get("draft_ids", []),
            file_sha256=data["file_sha256"],
            records_sha256_chain=data["records_sha256_chain"],
            audit_events_count=data.get("audit_events_count", 0),
            audit_events_sha256=data.get("audit_events_sha256", ""),
            exported_by=data.get("exported_by", "system"),
            format_version=data.get("format_version", AUDIT_RECEIPT_VERSION),
            compliance_statement=data.get("compliance_statement", COMPLIANCE_DISCLAIMER),
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> AuditReceipt:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


@dataclass
class AuditVerificationResult:
    """Detailed result of verifying an export file against its audit receipt and database."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "details": self.details,
        }


def compute_canonical_draft_hash(draft: Union[DraftMessage, Dict[str, Any]]) -> str:
    """Computes deterministic SHA-256 digest of a draft record's commercial attributes."""
    if isinstance(draft, DraftMessage):
        d_id = draft.draft_id
        lead_id = draft.lead_id
        sku = draft.sku or ""
        lang = draft.language
        app_status = draft.approval_status.value
        ver_status = draft.verification_report.status.value
        consent = "1" if draft.consent_to_contact else "0"
        evidence = draft.consent_evidence or ""
        rev_by = draft.reviewed_by or ""
        rev_at = draft.reviewed_at or ""
    else:
        d_id = str(draft.get("draft_id", ""))
        lead_id = str(draft.get("lead_id", ""))
        sku = str(draft.get("sku", "") or "")
        lang = str(draft.get("language", ""))
        app_status = str(draft.get("approval_status", ""))
        ver_rep = draft.get("verification_report")
        if isinstance(ver_rep, dict):
            ver_status = str(ver_rep.get("status", "") or "")
        else:
            ver_status = str(draft.get("verification_status", "") or "")
        consent = "1" if draft.get("consent_to_contact") in (1, "1", True) else "0"
        evidence = str(draft.get("consent_evidence", "") or "")
        rev_by = str(draft.get("reviewed_by", "") or "")
        rev_at = str(draft.get("reviewed_at", "") or "")

    canonical_str = f"{d_id}|{lead_id}|{sku}|{lang}|{app_status}|{ver_status}|{consent}|{evidence}|{rev_by}|{rev_at}"
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def compute_records_chain_hash(drafts: List[Union[DraftMessage, Dict[str, Any]]]) -> str:
    """Computes deterministic cumulative hash over all draft records in sorted order."""
    hashes = [compute_canonical_draft_hash(d) for d in drafts]
    hashes.sort()
    cumulative = "".join(hashes)
    return hashlib.sha256(cumulative.encode("utf-8")).hexdigest()


def compute_audit_events_hash(conn: sqlite3.Connection, draft_ids: List[str]) -> Tuple[int, str]:
    """Queries SQLite audit_log table and computes a cumulative digest of all review events."""
    if not draft_ids:
        return 0, hashlib.sha256(b"").hexdigest()

    placeholders = ",".join("?" for _ in draft_ids)
    cursor = conn.execute(
        f"""
        SELECT log_id, draft_id, action, actor, previous_status, new_status, timestamp
        FROM audit_log
        WHERE draft_id IN ({placeholders})
        ORDER BY log_id ASC
        """,
        draft_ids,
    )
    rows = cursor.fetchall()
    canonical_events = []
    for r in rows:
        c_str = f"{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4] or ''}|{r[5] or ''}|{r[6]}"
        canonical_events.append(c_str)

    combined = "\n".join(canonical_events)
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return len(rows), digest


class AuditExporter:
    """Exports and verifies tamper-evident audit trails with cryptographic receipts."""

    def __init__(self, ledger: ApprovalLedger):
        self.ledger = ledger

    def export_audit_trail(
        self,
        output_path: Union[str, Path],
        export_format: str = "csv",
        status_filter: Optional[ApprovalStatus] = None,
        exported_by: str = "Administrator",
        overwrite: bool = False,
    ) -> Tuple[Path, Path, AuditReceipt]:
        """Exports drafts and audit logs with cryptographic SHA-256 receipts."""
        out_p = Path(output_path).resolve()
        fmt = export_format.lower().strip()
        if fmt not in ("csv", "json"):
            raise ValueError(f"Unsupported export format '{export_format}'. Must be 'csv' or 'json'.")

        receipt_p = out_p.with_suffix(f".{fmt}.receipt.json") if not out_p.name.endswith(f".{fmt}") else Path(f"{out_p}.receipt.json")

        if not overwrite:
            if out_p.exists():
                raise FileExistsError(f"Export target file '{out_p}' already exists. Pass overwrite=True to replace.")
            if receipt_p.exists():
                raise FileExistsError(f"Export target receipt '{receipt_p}' already exists. Pass overwrite=True to replace.")

        drafts = self.ledger.list_drafts(status=status_filter)
        batch_id = f"EXP-AUDIT-{hashlib.sha256(str(datetime.now(timezone.utc)).encode()).hexdigest()[:8].upper()}"
        now_iso = datetime.now(timezone.utc).isoformat()

        out_p.parent.mkdir(parents=True, exist_ok=True)

        # 1. Write the export payload
        if fmt == "csv":
            self._write_csv_payload(out_p, drafts, batch_id)
        else:
            self._write_json_payload(out_p, drafts, batch_id, now_iso)

        # 2. Compute file-level SHA-256 hash
        with open(out_p, "rb") as f:
            file_sha256 = hashlib.sha256(f.read()).hexdigest()

        # 3. Compute record-level cumulative hash
        records_chain_hash = compute_records_chain_hash(drafts)

        # 4. Compute SQLite audit log events hash
        draft_ids = [d.draft_id for d in drafts]
        with self.ledger._get_connection() as conn:
            events_count, events_hash = compute_audit_events_hash(conn, draft_ids)

        # 5. Build and save receipt
        receipt = AuditReceipt(
            batch_id=batch_id,
            exported_at=now_iso,
            export_file=out_p.name,
            format=fmt,
            record_count=len(drafts),
            draft_ids=draft_ids,
            file_sha256=file_sha256,
            records_sha256_chain=records_chain_hash,
            audit_events_count=events_count,
            audit_events_sha256=events_hash,
            exported_by=exported_by,
        )
        receipt.save(receipt_p)

        return out_p, receipt_p, receipt

    def _write_csv_payload(self, path: Path, drafts: List[DraftMessage], batch_id: str) -> None:
        fieldnames = [
            "batch_id",
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
            "verification_status",
            "consent_to_contact",
            "consent_evidence",
            "detected_dialect",
            "query_intents",
            "fuzzy_match_notes",
        ]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for d in drafts:
                intents_str = ";".join(getattr(d, "query_intents", []) or [])
                raw_row = {
                    "batch_id": batch_id,
                    "draft_id": d.draft_id,
                    "lead_id": d.lead_id,
                    "customer_name": d.customer_name or "",
                    "contact_channel": d.contact_channel,
                    "contact_target": d.contact_target,
                    "sku": d.sku or "",
                    "language": d.language,
                    "subject": d.subject,
                    "body": d.body,
                    "reviewed_by": d.reviewed_by or "",
                    "reviewed_at": d.reviewed_at or "",
                    "approval_status": d.approval_status.value,
                    "verification_status": d.verification_report.status.value,
                    "consent_to_contact": 1 if d.consent_to_contact else 0,
                    "consent_evidence": d.consent_evidence,
                    "detected_dialect": getattr(d, "detected_dialect", None) or "",
                    "query_intents": intents_str,
                    "fuzzy_match_notes": getattr(d, "fuzzy_match_notes", None) or "",
                }
                # Sanitize cells to prevent CSV / formula injection
                sanitized_row = sanitize_csv_row(raw_row)
                writer.writerow(sanitized_row)

    def _write_json_payload(self, path: Path, drafts: List[DraftMessage], batch_id: str, exported_at: str) -> None:
        data = {
            "batch_id": batch_id,
            "exported_at": exported_at,
            "record_count": len(drafts),
            "drafts": [d.to_dict() for d in drafts],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def verify_audit_export(
    export_file_path: Union[str, Path],
    receipt_file_path: Optional[Union[str, Path]] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> AuditVerificationResult:
    """Verifies that an exported file strictly conforms to its cryptographic receipt and SQLite database."""
    file_p = Path(export_file_path).resolve()
    if not file_p.exists():
        return AuditVerificationResult(
            is_valid=False,
            errors=[f"Export file not found: {file_p}"],
            details={"file_path": str(file_p)},
        )

    # 1. Locate receipt
    if receipt_file_path:
        receipt_p = Path(receipt_file_path).resolve()
    else:
        # Check standard convention: <file>.receipt.json or <stem>.<format>.receipt.json
        candidate1 = Path(f"{file_p}.receipt.json")
        candidate2 = file_p.with_suffix(f"{file_p.suffix}.receipt.json")
        if candidate1.exists():
            receipt_p = candidate1
        elif candidate2.exists():
            receipt_p = candidate2
        else:
            return AuditVerificationResult(
                is_valid=False,
                errors=[f"Audit receipt not found for export file '{file_p.name}'. Looked for '{candidate1.name}'."],
                details={"file_path": str(file_p)},
            )

    if not receipt_p.exists():
        return AuditVerificationResult(
            is_valid=False,
            errors=[f"Audit receipt file not found: {receipt_p}"],
            details={"file_path": str(file_p), "receipt_path": str(receipt_p)},
        )

    errors = []
    details: Dict[str, Any] = {
        "file_path": str(file_p),
        "receipt_path": str(receipt_p),
    }

    try:
        receipt = AuditReceipt.load(receipt_p)
        details["batch_id"] = receipt.batch_id
        details["receipt_format_version"] = receipt.format_version
        details["exported_at"] = receipt.exported_at
    except Exception as e:
        return AuditVerificationResult(
            is_valid=False,
            errors=[f"Failed to parse audit receipt JSON: {e}"],
            details=details,
        )

    # 2. Check file byte-level SHA-256
    with open(file_p, "rb") as f:
        actual_file_sha256 = hashlib.sha256(f.read()).hexdigest()
    details["actual_file_sha256"] = actual_file_sha256
    details["expected_file_sha256"] = receipt.file_sha256

    if actual_file_sha256 != receipt.file_sha256:
        errors.append(
            f"FILE INTEGRITY FAILURE: Expected SHA-256 '{receipt.file_sha256}', but got '{actual_file_sha256}'."
        )

    # 3. Parse records and verify record-level chain
    parsed_records: List[Dict[str, Any]] = []
    try:
        if receipt.format == "csv" or file_p.suffix.lower() == ".csv":
            with open(file_p, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    parsed_records.append(desanitize_csv_row(row))
        else:
            with open(file_p, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            parsed_records = json_data.get("drafts", [])

        details["actual_record_count"] = len(parsed_records)
        details["expected_record_count"] = receipt.record_count

        if len(parsed_records) != receipt.record_count:
            errors.append(
                f"RECORD COUNT MISMATCH: Expected {receipt.record_count} records, found {len(parsed_records)}."
            )

        actual_records_chain = compute_records_chain_hash(parsed_records)
        details["actual_records_chain"] = actual_records_chain
        details["expected_records_chain"] = receipt.records_sha256_chain

        if actual_records_chain != receipt.records_sha256_chain:
            errors.append(
                f"RECORD CHAIN FAILURE: Cumulative hash '{actual_records_chain}' does not match receipt '{receipt.records_sha256_chain}'."
            )

    except Exception as e:
        errors.append(f"Failed to read and process records from export file: {e}")

    # 4. Cross-reference against SQLite database if provided
    if db_path:
        db_p = Path(db_path).resolve()
        details["db_path"] = str(db_p)
        if not db_p.exists():
            errors.append(f"SQLite database file not found: {db_p}")
        else:
            try:
                conn = sqlite3.connect(str(db_p))
                conn.row_factory = sqlite3.Row
                with conn:
                    # Check drafts existence and status
                    exported_draft_ids = [r.get("draft_id") for r in parsed_records if r.get("draft_id")]
                    for d_id in exported_draft_ids:
                        cursor = conn.execute("SELECT draft_id, approval_status FROM drafts WHERE draft_id = ?", (d_id,))
                        row = cursor.fetchone()
                        if not row:
                            errors.append(f"DATABASE DESYNC: Draft '{d_id}' exists in export but not in SQLite database.")

                    # Recompute SQLite audit log hash
                    db_events_count, db_events_hash = compute_audit_events_hash(conn, receipt.draft_ids)
                    details["db_events_count"] = db_events_count
                    details["expected_events_count"] = receipt.audit_events_count
                    details["db_events_sha256"] = db_events_hash
                    details["expected_events_sha256"] = receipt.audit_events_sha256

                    if db_events_hash != receipt.audit_events_sha256:
                        errors.append(
                            f"AUDIT LOG HASH MISMATCH: Database events hash '{db_events_hash}' does not match receipt '{receipt.audit_events_sha256}'."
                        )
            except Exception as e:
                errors.append(f"Database cross-referencing error: {e}")
            finally:
                conn.close()

    is_valid = len(errors) == 0
    return AuditVerificationResult(
        is_valid=is_valid,
        errors=errors,
        details=details,
    )
