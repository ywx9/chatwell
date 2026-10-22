from collections import Counter
import math

RECOMMENDATION_THRESHOLD = 0.7  # Provisional; not a calibrated probability.
DEFAULT_MAX_TURNS = 12


def select_speaker(characters, turns, recommendation, threshold=RECOMMENDATION_THRESHOLD):
    names = [c['name'] for c in characters]
    if isinstance(recommendation, dict):
        name = recommendation.get('name')
        confidence = recommendation.get('confidence')
        if (name in names and not isinstance(confidence, bool)
                and isinstance(confidence, (int, float)) and math.isfinite(confidence)
                and threshold <= confidence <= 1):
            return next(c for c in characters if c['name'] == name)
    counts = Counter(turn['character']['name'] for turn in turns)
    return min(characters, key=lambda c: counts[c['name']])
