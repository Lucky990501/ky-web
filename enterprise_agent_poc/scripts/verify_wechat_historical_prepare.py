"""Exact 06 evidence/classification regression; never a PRIMARY operator entry."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    args = parser.parse_args()
    raw = args.fixture.read_bytes()
    expected = '9b1eaa6ce86c9b0221eb2da36ce3566fad97e964f0887092717bc0c2159f2daf'
    if hashlib.sha256(raw).hexdigest() != expected: raise SystemExit('06 historical Fixture SHA mismatch')
    import test_wechat_historical_prepare as tests
    result = unittest.TextTestRunner(verbosity=1).run(tests.suite(raw))
    if args.fixture.read_bytes() != raw: raise SystemExit('Historical Fixture was changed')
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), exact_06_redacted_classification='REPLAYED',
        strict_full_native_status='NOT_PROVEN_REDACTED_ORIGINAL_HASH_INPUTS_UNAVAILABLE',
        historical_approval_temporal_validity='NOT_PROVEN', primary_changes=0, production_changes=0,
        provider_calls=0, wechat_calls=0, image_calls=0)))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__': raise SystemExit(main())
