from pricing import calculate_cost, PRICING_TABLE

def test_known_model_output_tokens():
    cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 1_000_000,
    })
    # $15.00 per million output = 1500 cents
    assert abs(cost - 1500.0) < 0.001

def test_known_model_input_tokens():
    cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    # $3.00 per million input = 300 cents
    assert abs(cost - 300.0) < 0.001

def test_cache_read_cheaper_than_input():
    cache_cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1_000_000,
        "output_tokens": 0,
    })
    input_cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    assert cache_cost < input_cost

def test_unknown_model_falls_back_to_sonnet():
    cost_unknown = calculate_cost("claude-unknown-99", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    cost_sonnet = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    assert cost_unknown == cost_sonnet

def test_all_tokens_combined():
    cost = calculate_cost("claude-opus-4-6", {
        "input_tokens": 1_000,
        "cache_creation_input_tokens": 1_000,
        "cache_read_input_tokens": 1_000,
        "output_tokens": 1_000,
    })
    # opus: input=$15/M, cache_write=$18.75/M, cache_read=$1.50/M, output=$75/M
    # each 1000 tokens = 0.001M
    expected = (15.0 + 18.75 + 1.50 + 75.0) * 0.001 * 100  # in cents
    assert abs(cost - expected) < 0.001

def test_pricing_table_has_required_models():
    assert "claude-sonnet-4-6" in PRICING_TABLE
    assert "claude-opus-4-6" in PRICING_TABLE
    assert "claude-haiku-4-5" in PRICING_TABLE
