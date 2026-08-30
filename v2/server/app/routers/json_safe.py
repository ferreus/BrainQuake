import math


def json_safe(value):
    """NaN/inf -> null. A channel with no usable baseline has an undefined score,
    and JSON has no way to spell NaN -- json.dumps emits a bare NaN token that
    JSON.parse rejects, blanking the whole panel."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
