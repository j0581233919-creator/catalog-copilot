"""Terminal User Interface for Human Review of CatalogCopilot sales drafts.

Provides interactive review, audit logging, inline editing with deterministic
re-verification, and strict enforcement of commercial and privacy guardrails.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Optional, List, TextIO, Union, Dict, Any

from .models import (
    ApprovalStatus,
    VerificationStatus,
    DraftMessage,
)
from .approval import ApprovalLedger
from .catalog import Catalog, CatalogLoader
from .verifier import CommercialClaimVerifier


class TerminalFormatter:
    """Handles ANSI color formatting with graceful fallback to plain text."""

    def __init__(self, use_color: Optional[bool] = None, output_stream: Optional[TextIO] = None):
        if use_color is not None:
            self.use_color = use_color
        else:
            stream = output_stream or sys.stdout
            is_tty = hasattr(stream, "isatty") and stream.isatty()
            no_color = bool(os.environ.get("NO_COLOR"))
            term_dumb = os.environ.get("TERM") == "dumb"
            self.use_color = is_tty and not no_color and not term_dumb

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self.use_color else text

    def dim(self, text: str) -> str:
        return f"\033[2m{text}\033[0m" if self.use_color else text

    def green(self, text: str) -> str:
        return f"\033[32m{text}\033[0m" if self.use_color else text

    def red(self, text: str) -> str:
        return f"\033[31m{text}\033[0m" if self.use_color else text

    def yellow(self, text: str) -> str:
        return f"\033[33m{text}\033[0m" if self.use_color else text

    def cyan(self, text: str) -> str:
        return f"\033[36m{text}\033[0m" if self.use_color else text

    def magenta(self, text: str) -> str:
        return f"\033[35m{text}\033[0m" if self.use_color else text

    def badge(self, status: Union[VerificationStatus, ApprovalStatus, str]) -> str:
        val = status.value if hasattr(status, "value") else str(status)
        if val in ("PASS", "APPROVED", "EDITED_AND_APPROVED"):
            return self.green(f"[{val}]")
        if val in ("FAIL", "REJECTED"):
            return self.red(f"[{val}]")
        if val in ("UNCERTAIN", "PENDING_REVIEW"):
            return self.yellow(f"[{val}]")
        return f"[{val}]"


class HumanReviewTUI:
    """Interactive Terminal User Interface for draft review and decision ledger integration."""

    def __init__(
        self,
        ledger: ApprovalLedger,
        catalog: Optional[Union[Catalog, str, Path]] = None,
        reviewer: Optional[str] = None,
        input_stream: Optional[TextIO] = None,
        output_stream: Optional[TextIO] = None,
        use_color: Optional[bool] = None,
        status_filter: Optional[ApprovalStatus] = ApprovalStatus.PENDING_REVIEW,
    ):
        self.ledger = ledger
        self.reviewer = str(reviewer or "").strip()
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout
        self.fmt = TerminalFormatter(use_color=use_color, output_stream=self.output_stream)
        self.status_filter = status_filter

        # Load catalog if path was passed
        if isinstance(catalog, (str, Path)):
            cat_path = Path(catalog)
            if cat_path.suffix.lower() == ".json":
                self.catalog: Optional[Catalog] = CatalogLoader.load_json(cat_path)
            else:
                self.catalog = CatalogLoader.load_csv(cat_path)
        else:
            self.catalog = catalog

        # Session tracking metrics
        self.metrics = {
            "approved": 0,
            "edited_and_approved": 0,
            "rejected": 0,
            "skipped": 0,
        }

    def _print(self, text: str = "") -> None:
        self.output_stream.write(f"{text}\n")
        self.output_stream.flush()

    def _prompt(self, prompt_text: str) -> str:
        self.output_stream.write(prompt_text)
        self.output_stream.flush()
        line = self.input_stream.readline()
        if line == "":
            raise EOFError("End of input stream reached.")
        return line.strip()

    def _readline_raw(self) -> Optional[str]:
        line = self.input_stream.readline()
        if line == "":
            return None
        return line.rstrip("\r\n")

    def ensure_reviewer_identity(self) -> str:
        """Ensures a reviewer name is available for audit logging."""
        while not self.reviewer:
            try:
                name = self._prompt(self.fmt.bold("Enter reviewer identity/name (required for audit log): "))
                if name:
                    self.reviewer = name
                else:
                    self._print(self.fmt.red("Reviewer identity cannot be empty."))
            except (EOFError, KeyboardInterrupt):
                raise
        return self.reviewer

    def format_draft_view(self, draft: DraftMessage, index: int, total: int) -> str:
        """Formats the full terminal view of a draft message."""
        lines: List[str] = []
        width = 78
        sep_double = "=" * width
        sep_single = "-" * width

        # Header
        lines.append(sep_double)
        title = f" CATALOGCOPILOT HUMAN REVIEW [Draft {index + 1} of {total}] "
        lines.append(self.fmt.bold(f"{title:^{width}}"))
        lines.append(sep_double)

        # Lead & Customer Context
        lines.append(f"{self.fmt.bold('Draft ID:'):<22} {draft.draft_id}")
        lines.append(f"{self.fmt.bold('Lead ID:'):<22} {draft.lead_id} ({draft.customer_name})")
        lines.append(f"{self.fmt.bold('Channel & Target:'):<22} {draft.contact_channel} -> {draft.contact_target}")
        
        # Catalog Item Context
        sku_display = draft.sku or "N/A"
        item_details = ""
        if self.catalog and draft.sku:
            item = self.catalog.get_item(draft.sku)
            if item:
                stock_tag = f"{item.stock_quantity} in stock" if item.in_stock else "OUT OF STOCK"
                item_details = f" | {item.title_en} ({item.price:.2f} {item.currency}, {stock_tag})"
        lines.append(f"{self.fmt.bold('Target SKU:'):<22} {sku_display}{item_details}")

        # Language
        lines.append(f"{self.fmt.bold('Language:'):<22} {draft.language.upper()}")

        # Consent Status
        if draft.consent_to_contact:
            evidence_str = f" (Evidence: {draft.consent_evidence})" if draft.consent_evidence else ""
            consent_badge = self.fmt.green("[OPT-IN CONFIRMED]") + evidence_str
        else:
            consent_badge = self.fmt.red("[NO CONSENT / REVOKED - OUTREACH PROHIBITED]")
        lines.append(f"{self.fmt.bold('Consent Status:'):<22} {consent_badge}")

        # Customer Inquiry
        inquiry_display = getattr(draft, "inquiry_text", "") or "(No inquiry text recorded)"
        lines.append(f"{self.fmt.bold('Customer Inquiry:'):<22} \"{inquiry_display}\"")

        # Proposed Draft Message
        lines.append(sep_single)
        lines.append(self.fmt.bold(" PROPOSED OUTBOUND DRAFT MESSAGE "))
        lines.append(sep_single)
        lines.append(f"{self.fmt.bold('Subject:')} {draft.subject}")
        lines.append(f"{self.fmt.bold('Body:')}")
        for body_line in draft.body.splitlines():
            lines.append(f"  {body_line}")

        # Deterministic Claim Verification Report
        lines.append(sep_single)
        report = draft.verification_report
        status_badge = self.fmt.badge(report.status)
        lines.append(f"{self.fmt.bold('COMMERCIAL CLAIM VERIFICATION REPORT:')} Status: {status_badge}")
        
        if report.checks:
            lines.append(self.fmt.dim("Claim Checks:"))
            for c in report.checks:
                check_icon = self.fmt.green("[PASS]") if c.is_valid else self.fmt.red("[FAIL]")
                lines.append(f"  {check_icon} {c.claim_type.value:<20}: {c.notes}")

        if report.reasons:
            lines.append(self.fmt.red(self.fmt.bold("Detected Issues / Guardrail Violations:")))
            for r in report.reasons:
                lines.append(f"  {self.fmt.red('!')} {r}")

        # Review Status
        lines.append(sep_single)
        approval_badge = self.fmt.badge(draft.approval_status)
        lines.append(f"{self.fmt.bold('Approval Status:'):<22} {approval_badge}")
        if draft.reviewed_by:
            lines.append(f"{self.fmt.bold('Reviewed By:'):<22} {draft.reviewed_by} at {draft.reviewed_at}")
        if draft.reviewer_notes:
            lines.append(f"{self.fmt.bold('Reviewer Notes:'):<22} {draft.reviewer_notes}")
        lines.append(sep_double)

        return "\n".join(lines)

    def handle_approve(self, draft: DraftMessage) -> bool:
        """Executes draft approval with strict validation guardrails."""
        reviewer = self.ensure_reviewer_identity()

        # Pre-check guardrails for friendly feedback
        if not draft.consent_to_contact:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(">>> [APPROVAL BLOCKED] Outreach strictly prohibited: customer has no opt-in consent!")))
            return False

        if draft.verification_report.status != VerificationStatus.PASS:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(
                f">>> [APPROVAL BLOCKED] Draft verification status is '{draft.verification_report.status.value}'. Only PASS drafts can be approved."
            )))
            return False

        try:
            notes_prompt = self._prompt("Optional approval notes (press Enter to skip): ")
            notes = notes_prompt.strip() or None
        except EOFError:
            notes = None

        try:
            updated = self.ledger.approve(draft_id=draft.draft_id, reviewer=reviewer, notes=notes)
            draft.approval_status = updated.approval_status
            draft.reviewed_by = updated.reviewed_by
            draft.reviewed_at = updated.reviewed_at
            draft.reviewer_notes = updated.reviewer_notes
            self.metrics["approved"] += 1
            self._print()
            self._print(self.fmt.green(self.fmt.bold(f"✓ Draft '{draft.draft_id}' successfully APPROVED for dispatch!")))
            return True
        except Exception as e:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(f">>> [APPROVAL ERROR] {e}")))
            return False

    def handle_reject(self, draft: DraftMessage) -> bool:
        """Executes draft rejection with mandatory audit reason."""
        reviewer = self.ensure_reviewer_identity()

        self._print()
        reason = self._prompt(self.fmt.bold("Enter reason for rejection (required, or ':cancel' to abort): ")).strip()
        if not reason or reason.lower() == ":cancel":
            self._print(self.fmt.yellow("Rejection cancelled: reason is mandatory."))
            return False

        try:
            updated = self.ledger.reject(draft_id=draft.draft_id, reviewer=reviewer, reason=reason)
            draft.approval_status = updated.approval_status
            draft.reviewed_by = updated.reviewed_by
            draft.reviewed_at = updated.reviewed_at
            draft.reviewer_notes = updated.reviewer_notes
            self.metrics["rejected"] += 1
            self._print()
            self._print(self.fmt.yellow(self.fmt.bold(f"✓ Draft '{draft.draft_id}' successfully REJECTED. Reason: {reason}")))
            return True
        except Exception as e:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(f">>> [REJECTION ERROR] {e}")))
            return False

    def handle_edit(self, draft: DraftMessage) -> bool:
        """Handles human inline editing with deterministic re-verification against the catalog."""
        reviewer = self.ensure_reviewer_identity()

        if not self.catalog:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(">>> [EDIT ERROR] No catalog available for re-verification. Edit aborted.")))
            return False

        if not draft.consent_to_contact:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(">>> [EDIT BLOCKED] Customer has not granted contact consent. Outreach prohibited.")))
            return False

        self._print()
        self._print(self.fmt.bold("--- Current Message Body ---"))
        self._print(draft.body)
        self._print(self.fmt.bold("-----------------------------"))
        self._print("Enter new message body. Finish by entering '.' on its own line, or ':cancel' to abort:")

        new_body_lines: List[str] = []
        while True:
            line = self._readline_raw()
            if line is None:
                # EOF reached
                break
            stripped = line.strip()
            if stripped == ":cancel":
                self._print(self.fmt.yellow("Edit cancelled."))
                return False
            if stripped == ".":
                break
            new_body_lines.append(line)

        new_body = "\n".join(new_body_lines).strip()
        if not new_body:
            self._print(self.fmt.yellow("Edit cancelled: new body cannot be empty."))
            return False

        try:
            edit_notes = self._prompt("Optional note explaining the edit (press Enter for default): ").strip()
            notes = edit_notes or f"Human edit and re-verification by {reviewer}"
        except EOFError:
            notes = f"Human edit and re-verification by {reviewer}"

        try:
            updated = self.ledger.edit_and_approve(
                draft_id=draft.draft_id,
                new_body=new_body,
                reviewer=reviewer,
                catalog=self.catalog,
                notes=notes,
            )
            draft.body = updated.body
            draft.verification_report = updated.verification_report
            draft.approval_status = updated.approval_status
            draft.reviewed_by = updated.reviewed_by
            draft.reviewed_at = updated.reviewed_at
            draft.reviewer_notes = updated.reviewer_notes
            self.metrics["edited_and_approved"] += 1
            self._print()
            self._print(self.fmt.green(self.fmt.bold(
                f"✓ Draft '{draft.draft_id}' deterministically re-verified (PASS) and EDITED_AND_APPROVED!"
            )))
            return True
        except ValueError as e:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(f">>> [GUARDRAIL REJECTION] Edit failed deterministic verification:")))
            self._print(self.fmt.red(f"    {e}"))
            self._print(self.fmt.yellow("    Draft remains in PENDING_REVIEW with original text."))
            return False
        except Exception as e:
            self._print()
            self._print(self.fmt.red(self.fmt.bold(f">>> [EDIT ERROR] {e}")))
            return False

    def print_summary(self, total_pending_remaining: int) -> None:
        """Prints a structured summary of the review session."""
        self._print()
        self._print(self.fmt.bold("=" * 50))
        self._print(self.fmt.bold(" REVIEW SESSION SUMMARY "))
        self._print(self.fmt.bold("=" * 50))
        self._print(f"  Approved:            {self.fmt.green(str(self.metrics['approved']))}")
        self._print(f"  Edited & Approved:   {self.fmt.green(str(self.metrics['edited_and_approved']))}")
        self._print(f"  Rejected:            {self.fmt.red(str(self.metrics['rejected']))}")
        self._print(f"  Remaining Pending:   {self.fmt.yellow(str(total_pending_remaining))}")
        self._print(self.fmt.bold("=" * 50))

    def run(self) -> int:
        """Main interactive review loop."""
        try:
            drafts = self.ledger.list_drafts(status=self.status_filter)
            if not drafts:
                self._print(self.fmt.yellow("No drafts found matching the review filter."))
                return 0

            current_idx = 0
            while 0 <= current_idx < len(drafts):
                draft = drafts[current_idx]
                self._print(self.format_draft_view(draft, current_idx, len(drafts)))

                # Action prompt
                if draft.approval_status == ApprovalStatus.PENDING_REVIEW:
                    actions_text = (
                        f"{self.fmt.bold('Actions:')} "
                        f"[{self.fmt.green('A')}]pprove | "
                        f"[{self.fmt.red('R')}]eject | "
                        f"[{self.fmt.cyan('E')}]dit & Re-verify | "
                        f"[{self.fmt.bold('N')}]ext | "
                        f"[{self.fmt.bold('P')}]rev | "
                        f"[{self.fmt.bold('Q')}]uit"
                    )
                else:
                    actions_text = (
                        f"{self.fmt.bold('Actions:')} "
                        f"[{self.fmt.bold('N')}]ext | "
                        f"[{self.fmt.bold('P')}]rev | "
                        f"[{self.fmt.bold('Q')}]uit "
                        f"{self.fmt.dim(f'(Already {draft.approval_status.value})')}"
                    )

                self._print(actions_text)
                try:
                    choice = self._prompt("Choose action: ").strip().upper()
                except (EOFError, KeyboardInterrupt):
                    self._print("\nReview session terminated.")
                    break

                if choice == "A":
                    if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
                        self._print(self.fmt.yellow(f"Draft is already {draft.approval_status.value}."))
                    else:
                        success = self.handle_approve(draft)
                        if success and current_idx < len(drafts) - 1:
                            current_idx += 1
                elif choice == "R":
                    if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
                        self._print(self.fmt.yellow(f"Draft is already {draft.approval_status.value}."))
                    else:
                        success = self.handle_reject(draft)
                        if success and current_idx < len(drafts) - 1:
                            current_idx += 1
                elif choice == "E":
                    if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
                        self._print(self.fmt.yellow(f"Draft is already {draft.approval_status.value}."))
                    else:
                        success = self.handle_edit(draft)
                        if success and current_idx < len(drafts) - 1:
                            current_idx += 1
                elif choice == "N":
                    if current_idx < len(drafts) - 1:
                        current_idx += 1
                    else:
                        self._print(self.fmt.yellow("Already at the last draft."))
                elif choice == "P":
                    if current_idx > 0:
                        current_idx -= 1
                    else:
                        self._print(self.fmt.yellow("Already at the first draft."))
                elif choice == "Q":
                    break
                else:
                    self._print(self.fmt.red(f"Unknown action '{choice}'. Valid choices: A, R, E, N, P, Q"))

            # Calculate remaining pending
            remaining_pending = len(self.ledger.list_drafts(status=ApprovalStatus.PENDING_REVIEW))
            self.print_summary(remaining_pending)
            return 0

        except (KeyboardInterrupt, EOFError):
            self._print("\nReview session interrupted.")
            remaining_pending = len(self.ledger.list_drafts(status=ApprovalStatus.PENDING_REVIEW))
            self.print_summary(remaining_pending)
            return 0
        except Exception as e:
            self._print(self.fmt.red(f"TUI error: {e}"))
            return 1
