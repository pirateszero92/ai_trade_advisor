import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_smc import (
    test_smc_engine_initialization,
    test_smc_analysis_structure,
    test_premium_discount_calculation,
    test_fvg_detection,
)

if __name__ == "__main__":
    test_smc_engine_initialization()
    print("[✓] test_smc_engine_initialization: PASSED")
    test_smc_analysis_structure()
    print("[✓] test_smc_analysis_structure: PASSED")
    test_premium_discount_calculation()
    print("[✓] test_premium_discount_calculation: PASSED")
    test_fvg_detection()
    print("[✓] test_fvg_detection: PASSED")
    print("\n>>> ALL SMC ENGINE TESTS PASSED! <<<")
