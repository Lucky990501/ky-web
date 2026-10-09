"""Complete 06 four-record metadata regression; no live authority or network."""
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
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--prepare-fixture', type=Path, required=True)
    args = parser.parse_args()
    raw, old = args.audit.read_bytes(), args.prepare_fixture.read_bytes()
    def need(condition, code):
        if not condition: raise ValueError(code)
    need(hashlib.sha256(raw).hexdigest() == '1fd7e443b4ada1861ea1f025a22dca7d1f7b7cb7796b829964542008ff5f9500', '06 audit identity mismatch')
    need(hashlib.sha256(old).hexdigest() == '9b1eaa6ce86c9b0221eb2da36ce3566fad97e964f0887092717bc0c2159f2daf', '06 prior qualification identity mismatch')
    import test_wechat_historical_actions as tests
    result = unittest.TextTestRunner(verbosity=1).run(tests.suite(raw, old))
    need(args.audit.read_bytes() == raw and args.prepare_fixture.read_bytes() == old, '06 immutable evidence changed')
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        observed_records=4, static_conflicts=5, evidence='EXACT_06_ALL_RECORDS_METADATA_REPLAY_WITH_EXPLICIT_PREREQUISITE_DOUBLES',
        native_primary_status='NOT_REVERIFIED', primary_install_authority='NOT_GRANTED', real_runtime_test='NOT_EXECUTED',
        primary_changes=0, production_changes=0, provider_calls=0, wechat_calls=0, image_calls=0)))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__': raise SystemExit(main())
