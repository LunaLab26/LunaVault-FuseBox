"""run_full_cycle.py — drives md5_matrix_test.run_matrix() across both the
small (4-clip) and large (9-clip) real source folders in one unattended pass,
writing progress/results/summary files after every single test so results
can be inspected mid-run at any time. See md5_matrix_test.py for the actual
per-test mechanics.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from md5_matrix_test import run_matrix

WORK_DIR = Path("G:/Claude cowork/20260703 - multicam video archive test/MD5 recover test/02 merged")
SMALL_FOLDER = Path("G:/Claude cowork/20260703 - multicam video archive test/MD5 recover test/01 input")
LARGE_FOLDER = Path("G:/Claude cowork/20260703 - multicam video archive test")

if __name__ == "__main__":
    print("=" * 70)
    print("PHASE 1: small folder (4 clips)")
    print("=" * 70)
    small_results = run_matrix(SMALL_FOLDER, WORK_DIR, tag="small")

    print("\n" + "=" * 70)
    print("PHASE 2: large folder (9 clips)")
    print("=" * 70)
    large_results = run_matrix(LARGE_FOLDER, WORK_DIR, tag="large")

    all_results = small_results + large_results
    failed = [r for r in all_results if r.get("status") != "pass"]
    print("\n" + "=" * 70)
    print(f"FULL CYCLE COMPLETE: {len(all_results) - len(failed)}/{len(all_results)} tests passed")
    if failed:
        print("FAILED:")
        for r in failed:
            print(f"  {r['test_id']}: {r.get('status')} — {r.get('verify_summary') or r.get('error')}")
    print("=" * 70)
