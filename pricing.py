from dataclasses import dataclass

@dataclass
class ModelPricing:
    input_per_m: float        # USD per million input tokens
    cache_read_per_m: float   # USD per million cache-read tokens
    cache_write_per_m: float  # USD per million cache-creation tokens
    output_per_m: float       # USD per million output tokens

PRICING_TABLE: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(3.00, 0.30, 3.75, 15.00),
    "claude-opus-4-6":   ModelPricing(15.00, 1.50, 18.75, 75.00),
    "claude-haiku-4-5":  ModelPricing(0.80, 0.08, 1.00, 4.00),
}

_DEFAULT = PRICING_TABLE["claude-sonnet-4-6"]


def calculate_cost(model: str, usage: dict) -> float:
    """Return estimated cost in cents."""
    p = PRICING_TABLE.get(model, _DEFAULT)
    usd = (
        usage.get("input_tokens", 0) * p.input_per_m / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * p.cache_read_per_m / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * p.cache_write_per_m / 1_000_000
        + usage.get("output_tokens", 0) * p.output_per_m / 1_000_000
    )
    return usd * 100  # convert to cents
