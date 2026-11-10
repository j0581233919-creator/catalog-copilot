"""Comprehensive unit, integration, and security tests for Chatwoot Webhook Integration (CC-003)."""

import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from catalog_copilot.models import (
    CatalogItem,
    DraftMessage,
    ApprovalStatus,
    VerificationStatus,
    VerificationReport,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.chatwoot import (
    ChatwootApiClient,
    ChatwootWebhookHandler,
    MockChatwootTransport,
    create_chatwoot_server,
)


class TestChatwootApiClient(unittest.TestCase):

    def setUp(self):
        self.transport = MockChatwootTransport()
        self.client = ChatwootApiClient(
            base_url="https://chatwoot.example.com",
            account_id=42,
            api_access_token="secret_token_xyz",
            transport=self.transport,
        )

    def test_send_private_note(self):
        res = self.client.send_private_note(conversation_id=101, content="Internal agent note")
        self.assertEqual(len(self.transport.sent_messages), 1)
        sent = self.transport.sent_messages[0]
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["url"], "https://chatwoot.example.com/api/v1/accounts/42/conversations/101/messages")
        self.assertEqual(sent["headers"]["api_access_token"], "secret_token_xyz")
        self.assertEqual(sent["payload"]["content"], "Internal agent note")
        self.assertTrue(sent["payload"]["private"])
        self.assertEqual(res["private"], True)

    def test_send_public_message(self):
        res = self.client.send_public_message(conversation_id=101, content="Public customer reply")
        self.assertEqual(len(self.transport.sent_messages), 1)
        sent = self.transport.sent_messages[0]
        self.assertEqual(sent["payload"]["content"], "Public customer reply")
        self.assertFalse(sent["payload"]["private"])
        self.assertEqual(res["private"], False)

    def test_transport_error_handling(self):
        def error_transport(method, url, headers, data):
            return 403, b'{"error": "Forbidden"}'

        err_client = ChatwootApiClient(
            base_url="https://chatwoot.example.com",
            account_id=42,
            api_access_token="invalid",
            transport=error_transport,
        )
        with self.assertRaises(RuntimeError) as ctx:
            err_client.send_private_note(101, "test")
        self.assertIn("403", str(ctx.exception))


class TestChatwootWebhookHandler(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_cw.db")
        self.ledger = ApprovalLedger(db_path=self.db_path)

        self.catalog = Catalog()
        self.catalog.add_item(CatalogItem(
            sku="COFFEE-ESP-01",
            title_en="Italian Espresso Beans 1kg",
            title_ar="بن اسبريسو إيطالي فاخر 1 كجم",
            price=120.0,
            currency="SAR",
            stock_quantity=45,
            is_active=True,
        ))
        self.catalog.add_item(CatalogItem(
            sku="PHONE-128",
            title_en="Smartphone 128GB",
            title_ar="هاتف ذكي 128 جيجا",
            price=1200.0,
            currency="SAR",
            stock_quantity=0,  # Out of stock
            is_active=True,
        ))

        self.transport = MockChatwootTransport()
        self.client = ChatwootApiClient(
            base_url="https://chatwoot.example.com",
            account_id=1,
            api_access_token="tok",
            transport=self.transport,
        )

        self.secret = "webhook_secret_key_123"
        self.handler = ChatwootWebhookHandler(
            catalog=self.catalog,
            ledger=self.ledger,
            api_client=self.client,
            webhook_secret=self.secret,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_signature_verification(self):
        raw = b'{"event": "message_created"}'
        valid_sig = hmac.new(self.secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

        # Valid with and without sha256= prefix
        self.assertTrue(self.handler.verify_signature(raw, valid_sig))
        self.assertTrue(self.handler.verify_signature(raw, f"sha256={valid_sig}"))

        # Invalid signatures
        self.assertFalse(self.handler.verify_signature(raw, "invalid_sig"))
        self.assertFalse(self.handler.verify_signature(raw, None))

    def test_incoming_customer_message_creates_draft_and_private_note(self):
        payload = {
            "event": "message_created",
            "id": 501,
            "message_type": "incoming",
            "private": False,
            "content": "هل لديكم بن اسبريسو وكم سعره؟ COFFEE-ESP-01",
            "conversation": {
                "id": 201,
                "channel": "Channel::Whatsapp",
                "custom_attributes": {
                    "consent_to_contact": True,
                    "consent_evidence": "optin:whatsapp_flow_20261111",
                    "requested_sku": "COFFEE-ESP-01",
                },
            },
            "sender": {
                "id": 901,
                "type": "contact",
                "name": "Nourah Al-Saud",
                "phone_number": "+966509998877",
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig = hmac.new(self.secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

        result = self.handler.process_webhook(payload, raw_body=raw_body, signature=sig)
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["verification_status"], "PASS")

        # Verify draft was persisted in ApprovalLedger
        draft_id = result["draft_id"]
        draft = self.ledger.get_draft(draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.customer_name, "Nourah Al-Saud")
        self.assertEqual(draft.contact_channel, "whatsapp")
        self.assertEqual(draft.sku, "COFFEE-ESP-01")
        self.assertEqual(draft.approval_status, ApprovalStatus.PENDING_REVIEW)
        self.assertEqual(draft.verification_report.status, VerificationStatus.PASS)

        # Verify private note was sent to Chatwoot
        self.assertEqual(len(self.transport.sent_messages), 1)
        sent = self.transport.sent_messages[0]
        self.assertTrue(sent["payload"]["private"])
        self.assertIn("CatalogCopilot Sales Proposal", sent["payload"]["content"])
        self.assertIn(draft_id, sent["payload"]["content"])
        self.assertIn("/approve", sent["payload"]["content"])

    def test_incoming_message_without_consent_blocks_approval(self):
        payload = {
            "event": "message_created",
            "id": 502,
            "message_type": "incoming",
            "private": False,
            "content": "سعر القهوة؟ COFFEE-ESP-01",
            "conversation": {
                "id": 202,
                "channel": "Channel::Email",
                "custom_attributes": {
                    "consent_to_contact": False,  # Missing consent!
                },
            },
            "sender": {
                "id": 902,
                "type": "contact",
                "name": "Unconsented User",
                "email": "user@example.com",
            },
        }
        result = self.handler.process_webhook(payload)
        self.assertEqual(result["status"], "draft_created")
        self.assertEqual(result["verification_status"], "FAIL")

        draft = self.ledger.get_draft(result["draft_id"])
        self.assertFalse(draft.consent_to_contact)

        # Attempting agent /approve must fail closed
        app_result = self.handler._execute_agent_approval(
            conversation_id="202",
            draft_id=draft.draft_id,
            agent_name="Agent Khalid",
        )
        self.assertEqual(app_result["status"], "blocked")
        self.assertEqual(app_result["reason"], "missing_consent")

        # Ensure NO public customer message was sent!
        public_messages = [m for m in self.transport.sent_messages if not m["payload"]["private"]]
        self.assertEqual(len(public_messages), 0)

    def test_incoming_message_invalid_sku_fails_verification_and_blocks_approval(self):
        payload = {
            "event": "message_created",
            "id": 503,
            "message_type": "incoming",
            "private": False,
            "content": "استفسار عن جهاز غير موجود UNKNOWN-SKU-999",
            "conversation": {
                "id": 203,
                "channel": "Channel::Chat",
                "custom_attributes": {
                    "consent_to_contact": True,
                    "consent_evidence": "chat_optin",
                    "requested_sku": "UNKNOWN-SKU-999",
                },
            },
            "sender": {
                "id": 903,
                "type": "contact",
                "name": "Saleh",
            },
        }
        result = self.handler.process_webhook(payload)
        self.assertEqual(result["status"], "draft_created")
        # UNKNOWN-SKU-999 does not exist in catalog -> verifier marks draft FAIL
        self.assertEqual(result["verification_status"], "FAIL")

        # Attempting approval must be blocked by guardrails
        app_result = self.handler._execute_agent_approval(
            conversation_id="203",
            draft_id=result["draft_id"],
            agent_name="Agent Khalid",
        )
        self.assertEqual(app_result["status"], "blocked")
        self.assertEqual(app_result["reason"], "verification_not_pass")

    def test_idempotent_duplicate_webhook_ignored(self):
        payload = {
            "event": "message_created",
            "id": 504,
            "message_type": "incoming",
            "private": False,
            "content": "استفسار COFFEE-ESP-01",
            "conversation": {"id": 204},
            "sender": {"type": "contact", "name": "Buyer"},
        }
        res1 = self.handler.process_webhook(payload)
        self.assertEqual(res1["status"], "draft_created")
        msg_count_1 = len(self.transport.sent_messages)

        # Duplicate delivery of same message ID
        res2 = self.handler.process_webhook(payload)
        self.assertEqual(res2["status"], "ignored")
        self.assertEqual(res2["reason"], "duplicate_event")
        self.assertEqual(len(self.transport.sent_messages), msg_count_1)

    def test_loop_prevention_agent_messages_ignored(self):
        # 1. Agent outgoing message
        payload_agent = {
            "event": "message_created",
            "id": 601,
            "message_type": "outgoing",
            "private": False,
            "content": "Hello customer",
            "conversation": {"id": 205},
            "sender": {"type": "user", "name": "Agent Tariq"},
        }
        res = self.handler.process_webhook(payload_agent)
        self.assertEqual(res["status"], "ignored")

        # 2. Agent normal private note (not a command)
        payload_note = {
            "event": "message_created",
            "id": 602,
            "message_type": "outgoing",
            "private": True,
            "content": "Customer asked about warranty earlier.",
            "conversation": {"id": 205},
            "sender": {"type": "user", "name": "Agent Tariq"},
        }
        res2 = self.handler.process_webhook(payload_note)
        self.assertEqual(res2["status"], "ignored")

    def test_agent_command_approve_success(self):
        # 1. Create a valid draft
        inquiry_payload = {
            "event": "message_created",
            "id": 505,
            "message_type": "incoming",
            "private": False,
            "content": "قهوة اسبريسو COFFEE-ESP-01",
            "conversation": {
                "id": 206,
                "custom_attributes": {
                    "consent_to_contact": True,
                    "consent_evidence": "web_optin",
                    "requested_sku": "COFFEE-ESP-01",
                },
            },
            "sender": {"type": "contact", "name": "Mona"},
        }
        inq_res = self.handler.process_webhook(inquiry_payload)
        draft_id = inq_res["draft_id"]

        # 2. Agent sends private note with command: "/approve cw_206_505"
        cmd_payload = {
            "event": "message_created",
            "id": 701,
            "message_type": "outgoing",
            "private": True,
            "content": f"/approve {draft_id}",
            "conversation": {"id": 206},
            "sender": {"type": "user", "name": "Supervisor Sara"},
        }
        cmd_res = self.handler.process_webhook(cmd_payload)
        self.assertEqual(cmd_res["status"], "approved_and_dispatched")
        self.assertEqual(cmd_res["draft_id"], draft_id)

        # Verify ledger state transitioned to DISPATCHED
        draft = self.ledger.get_draft(draft_id)
        self.assertEqual(draft.approval_status, ApprovalStatus.DISPATCHED)
        self.assertEqual(draft.reviewed_by, "Supervisor Sara")
        with self.ledger._get_connection() as conn:
            row = conn.execute("SELECT is_dispatched FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()
            self.assertEqual(row["is_dispatched"], 1)

        # Verify public message was sent to customer
        public_sent = [m for m in self.transport.sent_messages if not m["payload"]["private"]]
        self.assertEqual(len(public_sent), 1)
        self.assertEqual(public_sent[0]["payload"]["content"], draft.body)

    def test_agent_command_reject_success_with_reason(self):
        inquiry_payload = {
            "event": "message_created",
            "id": 506,
            "message_type": "incoming",
            "private": False,
            "content": "قهوة COFFEE-ESP-01",
            "conversation": {
                "id": 207,
                "custom_attributes": {
                    "consent_to_contact": True,
                    "consent_evidence": "web_optin",
                    "requested_sku": "COFFEE-ESP-01",
                },
            },
            "sender": {"type": "contact", "name": "Zayd"},
        }
        inq_res = self.handler.process_webhook(inquiry_payload)
        draft_id = inq_res["draft_id"]

        # Reject with reason
        cmd_payload = {
            "event": "message_created",
            "id": 702,
            "message_type": "outgoing",
            "private": True,
            "content": f"/reject {draft_id} Customer requested custom roast profile not in stock",
            "conversation": {"id": 207},
            "sender": {"type": "user", "name": "Supervisor Sara"},
        }
        cmd_res = self.handler.process_webhook(cmd_payload)
        self.assertEqual(cmd_res["status"], "rejected")

        # Verify ledger state
        draft = self.ledger.get_draft(draft_id)
        self.assertEqual(draft.approval_status, ApprovalStatus.REJECTED)
        self.assertEqual(draft.reviewer_notes, "Customer requested custom roast profile not in stock")

        # Verify audit log contains REJECT event
        history = self.ledger.get_audit_history(draft_id)
        actions = [h["action"] for h in history]
        self.assertIn("REJECT", actions)

    def test_agent_command_reject_missing_reason_blocked(self):
        inquiry_payload = {
            "event": "message_created",
            "id": 507,
            "message_type": "incoming",
            "private": False,
            "content": "قهوة COFFEE-ESP-01",
            "conversation": {"id": 208},
            "sender": {"type": "contact", "name": "User"},
        }
        inq_res = self.handler.process_webhook(inquiry_payload)
        draft_id = inq_res["draft_id"]

        # Empty reason
        cmd_payload = {
            "event": "message_created",
            "id": 703,
            "message_type": "outgoing",
            "private": True,
            "content": f"/reject {draft_id}",
            "conversation": {"id": 208},
            "sender": {"type": "user", "name": "Supervisor Sara"},
        }
        cmd_res = self.handler.process_webhook(cmd_payload)
        self.assertEqual(cmd_res["status"], "error")
        self.assertEqual(cmd_res["error"], "missing_rejection_reason")

        # Draft remains in PENDING_REVIEW
        draft = self.ledger.get_draft(draft_id)
        self.assertEqual(draft.approval_status, ApprovalStatus.PENDING_REVIEW)


class TestChatwootServer(unittest.TestCase):
    """End-to-end HTTP server testing on localhost."""

    def setUp(self):
        os.environ["CATALOG_COPILOT_QUIET_SERVER"] = "1"
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "server_test.db")
        self.ledger = ApprovalLedger(db_path=self.db_path)

        self.catalog = Catalog()
        self.catalog.add_item(CatalogItem(
            sku="COFFEE-ESP-01",
            title_en="Italian Espresso Beans 1kg",
            title_ar="بن اسبريسو إيطالي فاخر 1 كجم",
            price=120.0,
            currency="SAR",
            stock_quantity=45,
            is_active=True,
        ))

        self.transport = MockChatwootTransport()
        self.client = ChatwootApiClient(transport=self.transport)
        self.handler = ChatwootWebhookHandler(
            catalog=self.catalog,
            ledger=self.ledger,
            api_client=self.client,
            webhook_secret="serv_secret_xyz",
        )

        # Bind to port 0 for dynamic ephemeral port allocation
        self.server = create_chatwoot_server(handler=self.handler, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        time.sleep(0.05)  # Allow thread to bind socket

        # Build proxy-free opener for localhost calls
        import urllib.request
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1.0)
        self.tmp_dir.cleanup()

    def test_health_check_endpoint(self):
        req = Request(f"http://127.0.0.1:{self.port}/health", method="GET")
        with self.opener.open(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "healthy")

    def test_http_webhook_post_success(self):
        payload = {
            "event": "message_created",
            "id": 801,
            "message_type": "incoming",
            "private": False,
            "content": "بن اسبريسو COFFEE-ESP-01",
            "conversation": {
                "id": 301,
                "custom_attributes": {
                    "consent_to_contact": True,
                    "consent_evidence": "web_lead_optin",
                    "requested_sku": "COFFEE-ESP-01",
                },
            },
            "sender": {"type": "contact", "name": "Hind"},
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig = hmac.new(b"serv_secret_xyz", raw_body, hashlib.sha256).hexdigest()

        req = Request(
            f"http://127.0.0.1:{self.port}/webhook",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Chatwoot-Signature": sig,
            },
            method="POST",
        )
        with self.opener.open(req, timeout=3.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "draft_created")
            self.assertEqual(data["verification_status"], "PASS")

    def test_http_webhook_invalid_signature_returns_401(self):
        raw_body = b'{"event": "message_created"}'
        req = Request(
            f"http://127.0.0.1:{self.port}/webhook",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Chatwoot-Signature": "forged_signature_1234",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(req, timeout=3.0)
        self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
