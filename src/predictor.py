"""
FPL Prediction Model
Computes predicted points for each player based on multiple weighted factors.
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ─── Model weights ────────────────────────────────────────────────────────────
# These can be tuned over time based on backtesting accuracy
WEIGHTS = {
    "form": 0.25,
    "fixture": 0.20,
    "expected_stats": 0.20,
    "minutes": 0.15,
    "injury": 0.10,
    "fatigue": 0.10,
}

# ─── Position-specific base rates (avg points per game across the league) ─────
POSITION_BASE_RATES = {
    "GKP": 3.8,
    "DEF": 3.6,
    "MID": 3.4,
    "FWD": 3.2,
}

# ─── FDR multipliers (how fixture difficulty affects expected output) ──────────
# FDR 1 = massive boost, FDR 5 = significant penalty
FDR_MULTIPLIERS = {
    1: 1.35,
    2: 1.15,
    3: 1.00,
    4: 0.82,
    5: 0.65,
}

# ─── Fatigue factors for midweek European games ──────────────────────────────
EURO_FATIGUE = {
    "UCL": 0.88,    # Champions League = highest fatigue / rotation risk
    "UEL": 0.91,    # Europa League
    "UECL": 0.94,   # Conference League = lowest risk
    None: 1.00,      # No European football
}

# ─── Home advantage bonus ────────────────────────────────────────────────────
HOME_BONUS = 1.08
AWAY_PENALTY = 0.94


class PredictionModel:
    """Predicts FPL points for the upcoming gameweek."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or WEIGHTS

    def predict_all(self, players: list[dict]) -> list[dict]:
        """Add predictedPts to each player dict. Returns the same list, mutated."""
        for player in players:
            player["predictedPts"] = round(self.predict_player(player), 1)
            player["predictionBreakdown"] = self._get_breakdown(player)

        # Re-sort by predicted points
        players.sort(key=lambda p: p["predictedPts"], reverse=True)
        return players

    def predict_player(self, p: dict) -> float:
        """
        Compute predicted points for a single player.

        The model combines multiple signals:
        1. Form component: recent form with exponential weighting
        2. Fixture component: FDR-adjusted expectation
        3. Expected stats: xG + xA translated to point expectations
        4. Minutes component: availability and rotation risk
        5. Injury component: fitness and availability probability
        6. Fatigue component: European competition impact
        """
        position = p.get("position", "MID")
        base_rate = POSITION_BASE_RATES.get(position, 3.4)

        # 1. Form component
        form_score = self._form_component(p, base_rate)

        # 2. Fixture component
        fixture_score = self._fixture_component(p, base_rate)

        # 3. Expected stats component
        xstats_score = self._expected_stats_component(p, position)

        # 4. Minutes component
        minutes_score = self._minutes_component(p)

        # 5. Injury component
        injury_score = self._injury_component(p)

        # 6. Fatigue component
        fatigue_score = self._fatigue_component(p)

        # Weighted combination
        w = self.weights
        raw_prediction = (
            w["form"] * form_score
            + w["fixture"] * fixture_score
            + w["expected_stats"] * xstats_score
            + w["minutes"] * minutes_score * base_rate
            + w["injury"] * injury_score * base_rate
            + w["fatigue"] * fatigue_score * base_rate
        )

        # Apply home/away modifier
        next_fix = p.get("nextFixture", "")
        if "(H)" in next_fix:
            raw_prediction *= HOME_BONUS
        elif "(A)" in next_fix:
            raw_prediction *= AWAY_PENALTY

        # Floor at 0, cap at a reasonable max
        return max(0.0, min(raw_prediction, 18.0))

    def _form_component(self, p: dict, base_rate: float) -> float:
        """Score based on recent form with recency weighting."""
        form = p.get("form", 0)
        last5 = p.get("last5", [])

        if last5:
            # Exponentially weighted average of last 5 gameweeks
            decay = 0.82
            weights = [decay ** i for i in range(len(last5) - 1, -1, -1)]
            total_w = sum(weights)
            weighted_avg = sum(v * w for v, w in zip(last5, weights)) / total_w
            # Blend API form with our weighted calc
            return 0.6 * weighted_avg + 0.4 * form
        return form if form > 0 else base_rate

    def _fixture_component(self, p: dict, base_rate: float) -> float:
        """Score based on fixture difficulty rating."""
        fdr = p.get("fdr", 3)
        multiplier = FDR_MULTIPLIERS.get(fdr, 1.0)
        form = p.get("form", base_rate)
        return form * multiplier

    def _expected_stats_component(self, p: dict, position: str) -> float:
        """
        Translate xG and xA into expected FPL points.
        
        FPL scoring:
        - Goal: FWD=4, MID=5, DEF=6, GKP=6
        - Assist: 3 for all
        - Clean sheet: DEF/GKP=4, MID=1
        """
        xg = p.get("xG", 0)  # per 90
        xa = p.get("xA", 0)  # per 90

        goal_points = {"FWD": 4, "MID": 5, "DEF": 6, "GKP": 6}.get(position, 4)
        assist_points = 3

        expected_from_goals = xg * goal_points
        expected_from_assists = xa * assist_points

        # Clean sheet contribution for defenders
        cs_bonus = 0
        if position in ("DEF", "GKP"):
            # Rough estimate: use FDR as proxy for clean sheet probability
            fdr = p.get("fdr", 3)
            cs_probs = {1: 0.45, 2: 0.35, 3: 0.25, 4: 0.15, 5: 0.08}
            cs_prob = cs_probs.get(fdr, 0.25)
            cs_points = 4
            cs_bonus = cs_prob * cs_points
        elif position == "MID":
            fdr = p.get("fdr", 3)
            cs_probs = {1: 0.45, 2: 0.35, 3: 0.25, 4: 0.15, 5: 0.08}
            cs_prob = cs_probs.get(fdr, 0.25)
            cs_bonus = cs_prob * 1  # Midfielders get 1 point for CS

        # Base appearance points (2 if > 60 mins, 1 otherwise)
        appearance = 2.0

        return expected_from_goals + expected_from_assists + cs_bonus + appearance

    def _minutes_component(self, p: dict) -> float:
        """
        Score from 0-1 representing availability / rotation risk.
        A player who plays 90 every week = 1.0
        """
        recent_mins = p.get("minutesPlayed", [])
        if not recent_mins:
            return 0.5

        avg_mins = sum(recent_mins) / len(recent_mins)
        consistency = avg_mins / 90.0

        # Penalize high variance in minutes (rotation risk)
        if len(recent_mins) >= 3:
            import statistics
            stdev = statistics.stdev(recent_mins)
            rotation_penalty = max(0, 1 - (stdev / 45))
        else:
            rotation_penalty = 1.0

        return min(1.0, consistency * 0.7 + rotation_penalty * 0.3)

    def _injury_component(self, p: dict) -> float:
        """Score from 0-1 representing injury impact."""
        if not p.get("injured"):
            return 1.0

        chance = p.get("chanceOfPlaying")
        if chance is not None:
            return chance / 100.0

        # Default injury penalties by status
        status = p.get("injuryStatus", "a")
        penalties = {
            "a": 1.0,   # Available
            "d": 0.5,   # Doubtful
            "i": 0.0,   # Injured
            "s": 0.0,   # Suspended
            "u": 0.1,   # Unavailable
            "n": 0.8,   # Not in squad (loan etc.)
        }
        return penalties.get(status, 0.5)

    def _fatigue_component(self, p: dict) -> float:
        """Score from 0-1 representing European competition fatigue."""
        euro = p.get("euroComp")
        base = EURO_FATIGUE.get(euro, 1.0)

        # Additional penalty for high-minute players in European games
        recent_mins = p.get("minutesPlayed", [])
        if euro and recent_mins:
            avg = sum(recent_mins) / len(recent_mins)
            if avg > 85:
                base *= 0.95  # Extra fatigue for nailed starters

        return base

    def _get_breakdown(self, p: dict) -> dict:
        """Return the score breakdown for transparency."""
        position = p.get("position", "MID")
        base_rate = POSITION_BASE_RATES.get(position, 3.4)

        return {
            "form": round(self._form_component(p, base_rate), 2),
            "fixture": round(self._fixture_component(p, base_rate), 2),
            "expectedStats": round(self._expected_stats_component(p, position), 2),
            "minutes": round(self._minutes_component(p), 2),
            "injury": round(self._injury_component(p), 2),
            "fatigue": round(self._fatigue_component(p), 2),
        }


def backtest_model(
    historical_data: list[dict], model: PredictionModel | None = None
) -> dict:
    """
    Backtest the prediction model against historical gameweek data.
    
    Args:
        historical_data: List of player dicts with actual 'gwPoints' field.
        model: PredictionModel instance (uses defaults if None).
        
    Returns:
        Dictionary with accuracy metrics (MAE, RMSE, correlation).
    """
    model = model or PredictionModel()
    errors = []
    predictions = []
    actuals = []

    for player in historical_data:
        actual = player.get("gwPoints", 0)
        predicted = model.predict_player(player)

        error = predicted - actual
        errors.append(error)
        predictions.append(predicted)
        actuals.append(actual)

    n = len(errors)
    if n == 0:
        return {"error": "No data to backtest"}

    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e ** 2 for e in errors) / n)

    # Pearson correlation
    avg_p = sum(predictions) / n
    avg_a = sum(actuals) / n
    cov = sum((p - avg_p) * (a - avg_a) for p, a in zip(predictions, actuals)) / n
    std_p = math.sqrt(sum((p - avg_p) ** 2 for p in predictions) / n)
    std_a = math.sqrt(sum((a - avg_a) ** 2 for a in actuals) / n)
    correlation = cov / (std_p * std_a) if std_p * std_a > 0 else 0

    return {
        "sampleSize": n,
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "correlation": round(correlation, 3),
        "avgPredicted": round(avg_p, 2),
        "avgActual": round(avg_a, 2),
    }
