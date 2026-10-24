from collections import Counter
import math
import random

RECOMMENDATION_THRESHOLD = 0.75  # Recommendation score, not a calibrated probability.
DEFAULT_MAX_TURNS = 12


def candidates(characters, last_speaker=None):
    return [c for c in characters if c['name'] != last_speaker] or list(characters)


def select_speaker(characters, turns, recommendation, threshold=RECOMMENDATION_THRESHOLD):
    eligible = candidates(characters, turns[-1]['character']['name'] if turns else None)
    if isinstance(recommendation, dict):
        name = recommendation.get('name')
        confidence = recommendation.get('confidence')
        if (not isinstance(confidence, bool) and isinstance(confidence, (int, float))
                and math.isfinite(confidence) and threshold <= confidence <= 1):
            for character in eligible:
                if character['name'] == name:
                    return character
    counts = Counter(turn['character']['name'] for turn in turns)
    minimum = min(counts[c['name']] for c in eligible)
    weights = [1 / (1 + counts[c['name']] - minimum) for c in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]
