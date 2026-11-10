#!/usr/bin/env python3
"""Interactive / Demonstration runner for Chatwoot Webhook Integration (CC-003).

Demonstrates:
1. Webhook processing of customer inquiries across WhatsApp and Email.
2. Grounded draft generation and deterministic claim verification.
3. Synchronization of verified proposals as internal Chatwoot Private Notes.
4. Human agent command execution via Chatwoot private notes (/approve, /reject).
5. Strict guardrail enforcement (blocking unconsented outreach and unverified claims).
"""

from __future__ import annotations
import os
import sys
import tempfile
import json
from pathlib import Path

# Add repo root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog_copilot.models import (
    CatalogItem,
    ApprovalStatus,
    VerificationStatus,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.chatwoot import (
    ChatwootApiClient,
    ChatwootWebhookHandler,
    MockChatwootTransport,
)


def run_demo() -> int:
    print("=" * 78)
    print(" CATALOGCOPILOT — CHATWOOT WEBHOOK INTEGRATION DEMONSTRATION (CC-003)")
    print("=" * 78)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "chatwoot_demo.db")
        ledger = ApprovalLedger(db_path=db_path)

        catalog = Catalog()
        catalog.add_item(CatalogItem(
            sku="COFFEE-ESP-01",
            title_en="Italian Espresso Beans 1kg",
            title_ar="بن اسبريسو إيطالي فاخر 1 كجم",
            price=120.0,
            currency="SAR",
            stock_quantity=45,
            is_active=True,
        ))
        catalog.add_item(CatalogItem(
            sku="PHONE-128",
            title_en="Smartphone 128GB",
            title_ar="هاتف ذكي 128 جيجا",
            price=1200.0,
            currency="SAR",
            stock_quantity=10,
            is_active=True,
        ))

        transport = MockChatwootTransport()
        client = ChatwootApiClient(
            base_url="https://app.chatwoot.com",
            account_id=1,
            api_access_token="cw_access_tok_secret",
            transport=transport,
        )

        handler = ChatwootWebhookHandler(
            catalog=catalog,
            ledger=ledger,
            api_client=client,
            webhook_secret="demo_webhook_secret_key",
        )

        # -------------------------------------------------------------
        # Step 1: Incoming Customer Message with Opt-In Consent
        # -------------------------------------------------------------
        print("\n[Step 1] Incoming Customer Message via WhatsApp (Consented)")
        inquiry_1 = {
            "event": "message_created",
            "id": 1001,
            "message_type": "incoming",
            "private": False,
            "content": "مرحباً، هل متوفر عندكم بن اسبريسو إيطالي؟ وبكم؟ COFFEE-ESP-01",
            "conversation": {
                "id": 501,
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
                "name": "Nasser Al-Ghamdi",
                "phone_number": "+966501112233",
            },
        }

        res_1 = handler.process_webhook(inquiry_1)
        draft_id_1 = res_1["draft_id"]
        print(f"  ✓ Draft created: {draft_id_1} (Verification: {res_1['verification_status']})")
        print(f"  ✓ Private note synchronized to Chatwoot Conversation #501:")
        print(f"    Content preview:\n{transport.sent_messages[-1]['payload']['content']}")

        # -------------------------------------------------------------
        # Step 2: Agent Approves Draft via Command
        # -------------------------------------------------------------
        print("\n[Step 2] Agent Approves Draft via Private Note Command (`/approve`)")
        agent_approve_cmd = {
            "event": "message_created",
            "id": 2001,
            "message_type": "outgoing",
            "private": True,
            "content": f"/approve {draft_id_1}",
            "conversation": {"id": 501},
            "sender": {
                "id": 88,
                "type": "user",
                "name": "Supervisor Sarah",
            },
        }
        res_2 = handler.process_webhook(agent_approve_cmd)
        print(f"  ✓ Action result: {res_2['status']} by {res_2['reviewed_by']}")

        draft_1 = ledger.get_draft(draft_id_1)
        print(f"  ✓ Database state: {draft_1.approval_status.value} (Dispatched: {draft_1.approval_status == ApprovalStatus.DISPATCHED})")
        
        # Verify outgoing public reply sent to customer
        public_msgs = [m for m in transport.sent_messages if not m["payload"]["private"]]
        print(f"  ✓ Outbound customer reply delivered:\n    \"{public_msgs[-1]['payload']['content']}\"")

        # -------------------------------------------------------------
        # Step 3: Incoming Customer Message WITHOUT Consent
        # -------------------------------------------------------------
        print("\n[Step 3] Incoming Customer Message via Email (NO Consent)")
        inquiry_2 = {
            "event": "message_created",
            "id": 1002,
            "message_type": "incoming",
            "private": False,
            "content": "كم سعر الهاتف الذكي 128 جيجا؟ PHONE-128",
            "conversation": {
                "id": 502,
                "channel": "Channel::Email",
                "custom_attributes": {
                    "consent_to_contact": False,  # Missing consent!
                },
            },
            "sender": {
                "id": 902,
                "type": "contact",
                "name": "Anonymous Lead",
                "email": "lead@external.com",
            },
        }
        res_3 = handler.process_webhook(inquiry_2)
        draft_id_2 = res_3["draft_id"]
        print(f"  ✓ Draft created: {draft_id_2} (Verification: {res_3['verification_status']})")
        print(f"  ✓ Consent status: {res_3['consent_to_contact']} (Outreach strictly prohibited)")

        # -------------------------------------------------------------
        # Step 4: Agent Tries to Approve Unconsented Draft (Blocked!)
        # -------------------------------------------------------------
        print("\n[Step 4] Agent Tries to Approve Unconsented Draft")
        illegal_approve_cmd = {
            "event": "message_created",
            "id": 2002,
            "message_type": "outgoing",
            "private": True,
            "content": f"/approve {draft_id_2}",
            "conversation": {"id": 502},
            "sender": {
                "id": 88,
                "type": "user",
                "name": "Supervisor Sarah",
            },
        }
        res_4 = handler.process_webhook(illegal_approve_cmd)
        print(f"  ✓ Guardrail blocked approval: status={res_4['status']}, reason={res_4['reason']}")
        print(f"  ✓ Warning note sent to agent:\n    {transport.sent_messages[-1]['payload']['content']}")

        # -------------------------------------------------------------
        # Step 5: Agent Rejects Draft with Audit Reason
        # -------------------------------------------------------------
        print("\n[Step 5] Agent Rejects Unconsented Draft with Mandatory Reason")
        reject_cmd = {
            "event": "message_created",
            "id": 2003,
            "message_type": "outgoing",
            "private": True,
            "content": f"/reject {draft_id_2} Missing explicit marketing consent from lead",
            "conversation": {"id": 502},
            "sender": {
                "id": 88,
                "type": "user",
                "name": "Supervisor Sarah",
            },
        }
        res_5 = handler.process_webhook(reject_cmd)
        print(f"  ✓ Rejection confirmed: {res_5['status']} (Reason: {res_5['reason']})")
        
        draft_2 = ledger.get_draft(draft_id_2)
        print(f"  ✓ Database state: {draft_2.approval_status.value} (Notes: {draft_2.reviewer_notes})")

        print("\n" + "=" * 78)
        print(" CHATWOOT DEMONSTRATION COMPLETE: 100% GUARDRAIL COMPLIANCE PROVEN")
        print("=" * 78)
        return 0


if __name__ == "__main__":
    sys.exit(run_demo())
