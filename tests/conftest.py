"""The preserved flat NavPoint suite has its own complete parity runner.

Run all portable/integration tests with:
    SC_KB_REAL_DATA=/tmp/sc-kb-public-real python -m pytest -q tests

Then run EVERY upstream and ported NavPoint test independently with:
    python tools/qualify_navpoint.py --suite both

The latter stages isolated module namespaces and reports all common platform
and absent-fixture failures, plus any new differences. Collecting these flat
upstream tests in this process would mix their module stubs with real Wingman
imports and report collection errors instead of meaningful port qualification.
"""
collect_ignore = ["navpoint"]
