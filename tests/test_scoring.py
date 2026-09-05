from filingbench.scoring import (
    classify,
    is_scaling_error,
    values_agree,
    wilson_interval,
)

TRUTH = 391_035_000_000.0
OTHERS = [383_285_000_000.0, 394_328_000_000.0]


def test_exact_and_rounded_agreement():
    assert values_agree(TRUTH, TRUTH, "USD")
    assert values_agree(391_000_000_000.0, TRUTH, "USD")      # printed in billions
    assert not values_agree(380_000_000_000.0, TRUTH, "USD")


def test_per_share_uses_an_absolute_tolerance():
    assert values_agree(6.08, 6.08, "USD/shares")
    assert not values_agree(6.13, 6.08, "USD/shares")


def test_scaling_error_is_a_power_of_ten():
    assert is_scaling_error(391_035_000.0, TRUTH)
    assert is_scaling_error(391_035.0, TRUTH)
    assert not is_scaling_error(383_285_000_000.0, TRUTH)


def test_classification_separates_the_failure_kinds():
    assert classify(TRUTH, "ok", TRUTH, "USD", OTHERS) == "correct"
    assert classify(391_035.0, "ok", TRUTH, "USD", OTHERS) == "scaling_error"
    assert classify(383_285_000_000.0, "ok", TRUTH, "USD", OTHERS) == "wrong_period"
    assert classify(1_234.0, "ok", TRUTH, "USD", OTHERS) == "wrong_row"
    assert classify(None, "row_not_found", TRUTH, "USD", OTHERS) == "not_found"
    assert classify(None, "no_value_in_row", TRUTH, "USD", OTHERS) == "parse_error"


def test_wilson_interval_brackets_the_estimate():
    low, high = wilson_interval(90, 100)
    assert low < 0.90 < high
    assert 0.0 <= low and high <= 1.0
