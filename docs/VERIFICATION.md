# Verification & Quality Assurance Runbook

## Automated Test Execution

CatalogCopilot maintains a 100% automated test suite using Python's standard `unittest` framework:

```bash
# Execute entire test suite
python3 -m unittest discover tests -v
```

### Test Suite Structure
- `tests/test_catalog_models.py`: Catalog schema parsing, currency formatting, stock tracking, and discount validation.
- `tests/test_bilingual_grounding.py`: Arabic and English natural language tokenization, synonym matching, and catalog grounding.
- `tests/test_claim_verifier.py`: Automated assertion verification against catalog facts with truth value auditing.
- `tests/test_workflow_security.py`: State machine transitions, human approval gates, and escalation boundaries.
- `tests/test_audit_export.py`: SHA-256 tamper-evident export hashing, CSV formula injection defense, and JSON receipts.
- `tests/test_packaging_and_cli.py`: CLI invocation, exit codes, version consistency, and reproducible packaging.

## Verification Gate Checklist
1. All 137 unit and regression tests pass with 0 failures and 0 errors.
2. CLI subcommands (`catalog-copilot --help`, `catalog-copilot demo`) run cleanly without unhandled tracebacks.
3. Clean installation via `pip install .` in isolated virtual environment executes successfully without network access.
