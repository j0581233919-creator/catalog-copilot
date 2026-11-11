"""Focused Chatwoot Webhook Integration for CatalogCopilot (CC-003).

Provides:
1. Chatwoot REST API client with injectable offline transport.
2. Webhook payload parser and signature verification (HMAC-SHA256).
3. Grounded draft generation and deterministic verification for incoming inquiries.
4. Draft synchronization as private notes (invisible to customer).
5. Human agent command trigger (/approve, /reject) with strict guardrail enforcement.
6. Standalone standard-library HTTP webhook server.
"""

from __future__ import annotations
import hmac
import hashlib
import json
import os
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List, Union, Callable, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .models import (
    LeadInquiry,
    DraftMessage,
    ApprovalStatus,
    VerificationStatus,
    VerificationReport,
    CatalogItem,
)
from .catalog import Catalog
from .verifier import CommercialClaimVerifier
from .generator import DraftGenerator
from .approval import ApprovalLedger


class MockChatwootTransport:
    """In-memory mock transport for offline testing and deterministic verification."""

    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []

    @property
    def calls(self) -> List[Dict[str, Any]]:
        """Alias for sent_messages for test assertions."""
        return self.sent_messages

    def __call__(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data_bytes: Optional[bytes],
    ) -> Tuple[int, bytes]:
        payload = json.loads(data_bytes.decode("utf-8")) if data_bytes else {}
        self.sent_messages.append({
            "method": method,
            "url": url,
            "headers": headers,
            "payload": payload,
        })
        response_data = {
            "id": len(self.sent_messages),
            "content": payload.get("content", ""),
            "message_type": payload.get("message_type", "outgoing"),
            "private": payload.get("private", False),
            "created_at": 1700000000,
        }
        return 200, json.dumps(response_data).encode("utf-8")


class ChatwootApiClient:
    """Client for Chatwoot REST API with support for injectable transports."""

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        account_id: Union[int, str] = 1,
        api_access_token: str = "dummy_token",
        transport: Optional[Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes]]] = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.account_id = str(account_id)
        self.api_access_token = str(api_access_token or "").strip()
        self.transport = transport
        self.timeout = timeout

    def send_private_note(self, conversation_id: Union[int, str], content: str) -> Dict[str, Any]:
        """Posts an internal private note to a conversation (visible only to agents)."""
        return self._send_message(conversation_id=conversation_id, content=content, is_private=True)

    def send_public_message(self, conversation_id: Union[int, str], content: str) -> Dict[str, Any]:
        """Posts a public response message to a conversation (sent directly to the customer)."""
        return self._send_message(conversation_id=conversation_id, content=content, is_private=False)

    def _send_message(self, conversation_id: Union[int, str], content: str, is_private: bool) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/messages"
        payload = {
            "content": content,
            "message_type": "outgoing",
            "private": is_private,
        }
        return self._request(method="POST", url=endpoint, payload=payload)

    def _request(self, method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Content-Type": "application/json",
            "api_access_token": self.api_access_token,
            "User-Agent": "CatalogCopilot-ChatwootIntegration/1.0",
        }

        if self.transport is not None:
            status_code, resp_bytes = self.transport(method, url, headers, data_bytes)
            if status_code >= 400:
                raise RuntimeError(f"Chatwoot API error ({status_code}): {resp_bytes.decode('utf-8', errors='replace')}")
            return json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}

        # Standard library HTTP request
        req = Request(url=url, data=data_bytes, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                resp_bytes = resp.read()
                return json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Chatwoot API HTTP {e.code} error: {err_body}") from e
        except URLError as e:
            raise RuntimeError(f"Chatwoot API connection failed: {e.reason}") from e


class ChatwootWebhookHandler:
    """Core webhook handler orchestrating lead extraction, draft generation,
    deterministic verification, private note synchronization, and agent approval triggers.
    """

    def __init__(
        self,
        catalog: Catalog,
        ledger: ApprovalLedger,
        api_client: Optional[ChatwootApiClient] = None,
        webhook_secret: Optional[str] = None,
        verifier: Optional[CommercialClaimVerifier] = None,
        generator: Optional[DraftGenerator] = None,
    ):
        self.catalog = catalog
        self.ledger = ledger
        self.api_client = api_client
        self.webhook_secret = str(webhook_secret or "").strip() or None
        self.verifier = verifier or CommercialClaimVerifier()
        self.generator = generator or DraftGenerator()

    def verify_signature(self, raw_payload: bytes, signature_header: Optional[str]) -> bool:
        """Verifies HMAC-SHA256 signature for incoming webhooks."""
        if not self.webhook_secret:
            return True  # No secret configured, authentication bypassed by configuration

        if not signature_header:
            return False

        sig = signature_header.strip()
        if sig.startswith("sha256="):
            sig = sig[len("sha256="):]

        expected = hmac.new(self.webhook_secret.encode("utf-8"), raw_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig.lower(), expected.lower())

    def process_webhook(
        self,
        payload: Dict[str, Any],
        raw_body: Optional[bytes] = None,
        signature: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Processes an incoming Chatwoot webhook event."""
        # 1. Signature Verification
        if self.webhook_secret and raw_body is not None:
            if not self.verify_signature(raw_body, signature):
                return {"status": "unauthorized", "error": "Invalid HMAC signature"}

        # 2. Event Filtering
        event_name = str(payload.get("event") or payload.get("web_hook_event") or "").strip()
        if event_name != "message_created":
            return {"status": "ignored", "reason": f"unsupported_event_{event_name}"}

        # 3. Message Parsing
        message_id = str(payload.get("id") or "").strip()
        raw_type = payload.get("message_type")
        is_private = bool(payload.get("private", False))
        content = str(payload.get("content") or "").strip()
        conversation = payload.get("conversation") or {}
        conversation_id = str(conversation.get("id") or payload.get("conversation_id") or "").strip()
        sender = payload.get("sender") or {}
        sender_type = str(sender.get("type") or "").strip().lower()
        sender_name = str(sender.get("name") or "Agent").strip()

        if not conversation_id:
            return {"status": "error", "error": "Missing conversation_id"}

        # Normalize message type: Chatwoot uses 0 for incoming, 1 for outgoing
        is_incoming = (raw_type in ("incoming", 0, "0"))

        # 4. Agent Commands (/approve, /reject)
        if (sender_type == "user" or is_private) and content.startswith(("/", "!", ".")):
            cmd_line = content.lstrip("/!.")
            return self._handle_agent_command(
                conversation_id=conversation_id,
                command_text=cmd_line,
                agent_name=sender_name,
                message_id=message_id,
            )

        # 5. Ignore non-customer messages (outgoing agent messages, system notes)
        if not is_incoming or is_private or sender_type == "user":
            return {"status": "ignored", "reason": "not_incoming_customer_message"}

        # 6. Idempotency Check
        event_key = f"msg_{message_id}" if message_id else f"conv_{conversation_id}_{hash(content)}"
        if self.ledger.is_chatwoot_event_processed(event_key):
            return {"status": "ignored", "reason": "duplicate_event", "event_key": event_key}

        # 7. Customer Inquiry Processing
        return self._handle_customer_inquiry(
            conversation_id=conversation_id,
            message_id=message_id,
            content=content,
            conversation=conversation,
            sender=sender,
            event_key=event_key,
        )

    def _handle_customer_inquiry(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
        conversation: Dict[str, Any],
        sender: Dict[str, Any],
        event_key: str,
    ) -> Dict[str, Any]:
        """Extracts lead details, generates grounded draft, verifies claims,
        persists to ledger, and synchronizes a private note to the Chatwoot conversation.
        """
        custom_attrs = conversation.get("custom_attributes") or sender.get("custom_attributes") or {}

        # Consent extraction
        consent_to_contact = bool(custom_attrs.get("consent_to_contact", False))
        consent_evidence = str(custom_attrs.get("consent_evidence") or "").strip() or None

        # Contact and Channel extraction
        raw_channel = str(conversation.get("channel") or "").lower()
        if "whatsapp" in raw_channel:
            channel = "whatsapp"
        elif "email" in raw_channel:
            channel = "email"
        elif "sms" in raw_channel:
            channel = "sms"
        else:
            channel = "chat"

        customer_name = str(sender.get("name") or "Valued Customer").strip()
        contact_target = str(sender.get("phone_number") or sender.get("email") or f"chatwoot_conv_{conversation_id}").strip()

        # SKU extraction: first from custom_attributes, then text pattern
        requested_sku = str(custom_attrs.get("requested_sku") or "").strip() or None
        if not requested_sku:
            requested_sku = self._extract_sku_from_text(content)

        # Language preference
        pref_lang = str(custom_attrs.get("preferred_language") or "").strip().lower()
        if pref_lang not in ("ar", "en"):
            # Auto-detect from content: if Arabic characters exist, default to Arabic
            has_arabic = bool(re.search(r"[\u0621-\u064A]", content))
            pref_lang = "ar" if has_arabic else "en"

        lead = LeadInquiry(
            lead_id=f"cw_conv_{conversation_id}",
            customer_name=customer_name,
            contact_channel=channel,
            contact_target=contact_target,
            consent_to_contact=consent_to_contact,
            preferred_language=pref_lang,
            inquiry_text=content,
            requested_sku=requested_sku,
            consent_evidence=consent_evidence,
        )

        # Generate grounded draft
        draft = self.generator.generate_draft(lead=lead, catalog=self.catalog)
        draft.draft_id = f"cw_{conversation_id}_{message_id}" if message_id else f"cw_{conversation_id}_{int(os.urandom(4).hex(), 16)}"

        # Deterministic Verification
        report = self.verifier.verify(
            lead=lead,
            catalog=self.catalog,
            draft_text=draft.body,
            target_sku=draft.sku,
        )
        draft.verification_report = report

        # Persist draft to ApprovalLedger
        self.ledger.save_draft(draft)

        # Format and send Private Note to Chatwoot for human agent inspection
        private_note_text = self._format_private_note(draft)
        if self.api_client:
            try:
                self.api_client.send_private_note(conversation_id=conversation_id, content=private_note_text)
            except Exception as e:
                # Log error but preserve draft persistence
                sys.stderr.write(f"[ChatwootHandler] Failed to post private note: {e}\n")

        # Record idempotency event
        self.ledger.record_chatwoot_event(
            event_key=event_key,
            conversation_id=conversation_id,
            message_id=message_id,
            draft_id=draft.draft_id,
            status="DRAFT_GENERATED",
        )

        return {
            "status": "draft_created",
            "draft_id": draft.draft_id,
            "verification_status": draft.verification_report.status.value,
            "consent_to_contact": draft.consent_to_contact,
        }

    def _handle_agent_command(
        self,
        conversation_id: str,
        command_text: str,
        agent_name: str,
        message_id: str,
    ) -> Dict[str, Any]:
        """Handles human agent commands inside Chatwoot private notes."""
        tokens = command_text.strip().split()
        if not tokens:
            return {"status": "ignored", "reason": "empty_command"}

        action = tokens[0].lower()

        if action in ("approve", "app"):
            target_draft_id = tokens[1] if len(tokens) > 1 else None
            return self._execute_agent_approval(conversation_id, target_draft_id, agent_name)

        if action in ("reject", "rej"):
            # Syntax: /reject <draft_id> <reason...> OR /reject <reason...>
            if len(tokens) >= 2 and tokens[1].startswith("cw_"):
                target_draft_id = tokens[1]
                reason = " ".join(tokens[2:]).strip()
            elif len(tokens) >= 2:
                target_draft_id = None
                reason = " ".join(tokens[1:]).strip()
            else:
                target_draft_id = None
                reason = ""
            return self._execute_agent_rejection(conversation_id, target_draft_id, reason, agent_name)

        return {"status": "ignored", "reason": f"unrecognized_command_{action}"}

    def _execute_agent_approval(
        self,
        conversation_id: str,
        draft_id: Optional[str],
        agent_name: str,
    ) -> Dict[str, Any]:
        """Executes human approval with strict guardrail enforcement."""
        draft = self.ledger.get_draft(draft_id) if draft_id else self.ledger.get_latest_draft_for_conversation(conversation_id)
        if not draft:
            msg = f"❌ [CatalogCopilot] No pending draft found for conversation {conversation_id}."
            if self.api_client:
                self.api_client.send_private_note(conversation_id, msg)
            return {"status": "error", "error": "draft_not_found"}

        # Guardrail 1: Predecessor status check
        if draft.approval_status != ApprovalStatus.PENDING_REVIEW:
            msg = f"⚠️ [CatalogCopilot] Draft '{draft.draft_id}' is already {draft.approval_status.value}."
            if self.api_client:
                self.api_client.send_private_note(conversation_id, msg)
            return {"status": "error", "error": f"draft_already_{draft.approval_status.value}"}

        # Guardrail 2: Strict Consent Enforcement (NEVER send unsolicited outreach)
        if not draft.consent_to_contact:
            msg = (
                f"🚫 [APPROVAL BLOCKED] Customer has not granted opt-in contact consent.\n"
                f"Outreach strictly prohibited by privacy guardrails."
            )
            if self.api_client:
                self.api_client.send_private_note(conversation_id, msg)
            return {"status": "blocked", "reason": "missing_consent", "draft_id": draft.draft_id}

        # Guardrail 3: Strict Claim Verification Enforcement (Must be PASS)
        if draft.verification_report.status != VerificationStatus.PASS:
            issues = "; ".join(draft.verification_report.reasons) or "Detected unverified assertions."
            msg = (
                f"🚫 [APPROVAL BLOCKED] Verification status is '{draft.verification_report.status.value}'.\n"
                f"Only fully verified PASS drafts can be dispatched.\n"
                f"Issues: {issues}"
            )
            if self.api_client:
                self.api_client.send_private_note(conversation_id, msg)
            return {"status": "blocked", "reason": "verification_not_pass", "draft_id": draft.draft_id}

        # Approved! Transition in ApprovalLedger
        try:
            approved_draft = self.ledger.approve(
                draft_id=draft.draft_id,
                reviewer=agent_name,
                notes=f"Approved via Chatwoot command by {agent_name}",
            )

            # Send public reply to customer on Chatwoot
            if self.api_client:
                self.api_client.send_public_message(conversation_id=conversation_id, content=approved_draft.body)
                self.api_client.send_private_note(
                    conversation_id=conversation_id,
                    content=f"✅ [CatalogCopilot] Draft '{draft.draft_id}' approved and dispatched to customer by {agent_name}.",
                )

            # Mark dispatched in ledger
            self.ledger.mark_dispatched(draft.draft_id)

            return {
                "status": "approved_and_dispatched",
                "draft_id": draft.draft_id,
                "reviewed_by": agent_name,
            }
        except Exception as e:
            err_msg = f"❌ [CatalogCopilot] Approval failed: {e}"
            if self.api_client:
                self.api_client.send_private_note(conversation_id, err_msg)
            return {"status": "error", "error": str(e)}

    def _execute_agent_rejection(
        self,
        conversation_id: str,
        draft_id: Optional[str],
        reason: str,
        agent_name: str,
    ) -> Dict[str, Any]:
        """Executes human rejection with mandatory audit reason."""
        draft = self.ledger.get_draft(draft_id) if draft_id else self.ledger.get_latest_draft_for_conversation(conversation_id)
        if not draft:
            msg = f"❌ [CatalogCopilot] No pending draft found for conversation {conversation_id}."
            if self.api_client:
                self.api_client.send_private_note(conversation_id, msg)
            return {"status": "error", "error": "draft_not_found"}

        if not reason.strip():
            msg = (
                f"⚠️ [CatalogCopilot] Rejection reason is mandatory for audit logging.\n"
                f"Usage: `/reject {draft.draft_id} <reason>`"
            )
            if self.api_client:
                self.api_client.send_private_note(conversation_id, msg)
            return {"status": "error", "error": "missing_rejection_reason"}

        try:
            self.ledger.reject(draft_id=draft.draft_id, reviewer=agent_name, reason=reason)
            if self.api_client:
                self.api_client.send_private_note(
                    conversation_id=conversation_id,
                    content=f"🚫 [CatalogCopilot] Draft '{draft.draft_id}' REJECTED by {agent_name}.\nReason: {reason}",
                )
            return {
                "status": "rejected",
                "draft_id": draft.draft_id,
                "reviewer": agent_name,
                "reason": reason,
            }
        except Exception as e:
            err_msg = f"❌ [CatalogCopilot] Rejection failed: {e}"
            if self.api_client:
                self.api_client.send_private_note(conversation_id, err_msg)
            return {"status": "error", "error": str(e)}

    def _format_private_note(self, draft: DraftMessage) -> str:
        """Formats the proposed draft message and deterministic report into a structured private note."""
        status_val = draft.verification_report.status.value
        consent_badge = "[OPT-IN CONFIRMED]" if draft.consent_to_contact else "[NO CONSENT / PROHIBITED]"
        sku_val = draft.sku or "N/A"

        lines = [
            "🤖 **CatalogCopilot Sales Proposal**",
            f"• **Draft ID**: `{draft.draft_id}`",
            f"• **Target SKU**: `{sku_val}`",
            f"• **Verification**: `{status_val}`",
            f"• **Consent**: {consent_badge}",
        ]

        if getattr(draft, "detected_dialect", None):
            intents_str = ", ".join(getattr(draft, "query_intents", []) or [])
            lines.append(f"• **Dialect**: `{draft.detected_dialect}` ({intents_str or 'N/A'})")
        if getattr(draft, "fuzzy_match_notes", None):
            lines.append(f"• **Resolution**: {draft.fuzzy_match_notes}")

        lines.extend([
            "",
            "**Proposed Customer Reply:**",
            "```",
            draft.body,
            "```",
        ])

        if draft.verification_report.reasons:
            lines.append("**Detected Issues / Guardrail Violations:**")
            for r in draft.verification_report.reasons:
                lines.append(f"  • {r}")
            lines.append("")

        lines.extend([
            "**Agent Actions (reply with a private note):**",
            f"• `/approve {draft.draft_id}` — Approve and dispatch to customer",
            f"• `/reject {draft.draft_id} <reason>` — Reject proposal with audit reason",
        ])
        return "\n".join(lines)

    def _extract_sku_from_text(self, text: str) -> Optional[str]:
        """Scans inquiry text for exact SKU matches from the catalog."""
        if not text:
            return None
        text_upper = text.upper()
        # Direct lookup against catalog items
        for item in self.catalog.list_items():
            if item.sku.upper() in text_upper:
                return item.sku
        return None


class ChatwootServerHandler(BaseHTTPRequestHandler):
    """Standard library HTTP request handler for Chatwoot webhooks."""

    webhook_handler: Optional[ChatwootWebhookHandler] = None

    def do_GET(self) -> None:
        """Health check endpoint."""
        if self.path in ("/health", "/healthz", "/"):
            self._send_json(200, {"status": "healthy", "service": "catalog-copilot-chatwoot"})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        """Webhook listener endpoint."""
        if self.path not in ("/webhook", "/chatwoot/webhook", "/"):
            self._send_json(404, {"error": "Endpoint not found"})
            return

        if not self.webhook_handler:
            self._send_json(500, {"error": "Webhook handler not initialized"})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len) if content_len > 0 else b""
        signature = self.headers.get("X-Chatwoot-Signature") or self.headers.get("X-Signature")

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON payload"})
            return

        result = self.webhook_handler.process_webhook(
            payload=payload,
            raw_body=raw_body,
            signature=signature,
        )

        status_code = 200
        if result.get("status") == "unauthorized":
            status_code = 401
        elif result.get("status") == "error":
            status_code = 400

        self._send_json(status_code, result)

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logging during tests
        if not os.environ.get("CATALOG_COPILOT_QUIET_SERVER"):
            super().log_message(format, *args)


def create_chatwoot_server(
    handler: ChatwootWebhookHandler,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> HTTPServer:
    """Creates a configured HTTPServer listening for Chatwoot webhooks."""
    server_class = HTTPServer
    handler_class = ChatwootServerHandler
    handler_class.webhook_handler = handler
    return server_class((host, port), handler_class)
