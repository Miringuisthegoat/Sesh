import random


def delay(attempt: int, base: float = 2.0, cap: float = 300.0, rng=random.random) -> float:
    """Exponential backoff with jitter: between half and all of min(cap, base * 2^attempt) seconds."""
    ceiling = min(cap, base * 2 ** min(max(attempt, 0), 20))
    return ceiling / 2 + rng() * ceiling / 2
