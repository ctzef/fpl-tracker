"""
FPL Data Processor
Transforms raw FPL API data into enriched player records with computed stats.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

from .fetcher import FPLFetcher

logger = logging.getLogger(__name__)

# Position mapping
POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# FDR descriptions
FDR_LABELS = {1: "Very Easy", 2: "Easy", 3: "Medium", 4: "Hard", 5: "Very Hard"}


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _weighted_average(values: list[float], decay: float = 0.85) -> float:
    """Exponentially weighted average (most recent = highest weight)."""
    if not values:
        return 0.0
    weights = [decay ** i for i in range(len(values) - 1, -1, -1)]
    total_w = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total_w if total_w else 0.0


def _classify_momentum(recent_scores: list[float], form: float) -> str:
    """Classify a player's momentum trend."""
    if len(recent_scores) < 3:
        return "unknown"

    last3 = recent_scores[-3:]
    prev3 = recent_scores[-6:-3] if len(recent_scores) >= 6 else recent_scores[:3]

    avg_recent = statistics.mean(last3) if last3 else 0
    avg_prev = statistics.mean(prev3) if prev3 else 0

    if len(last3) >= 3:
        stdev = statistics.stdev(last3)
    else:
        stdev = 0

    # High variance = volatile
    if stdev > 4:
        return "volatile"

    diff = avg_recent - avg_prev
    if form >= 8 and diff >= 1:
        return "hot"
    elif diff >= 1.5:
        return "rising"
    elif diff <= -2:
        return "declining"
    elif diff <= -0.5:
        return "cooling"
    else:
        return "stable"


class FPLProcessor:
    """Processes raw FPL data into enriched player records."""

    def __init__(self, fetcher: FPLFetcher):
        self.fetcher = fetcher
        self._teams: dict[int, dict] = {}
        self._fixtures_by_gw: dict[int, list] = {}

    def process_all(self, top_n: int = 200) -> dict[str, Any]:
        """
        Main entry point. Returns a complete data structure for the dashboard.

        Returns:
            {
                "meta": {...},
                "players": [...],
                "fixtures": [...],
                "injuries": [...],
                "captain_picks": [...],
                "differentials": [...],
            }
        """
        logger.info("Processing FPL data...")

        self._teams = self.fetcher.get_teams()
        current_gw = self.fetcher.get_current_gameweek()
        next_gw = self.fetcher.get_next_gameweek()

        # Get all fixtures and index by gameweek
        all_fixtures = self.fetcher.get_fixtures()
        for f in all_fixtures:
            gw = f.get("event")
            if gw:
                self._fixtures_by_gw.setdefault(gw, []).append(f)

        # Process players
        raw_players = self.fetcher.get_players_raw()

        # Sort by total_points descending, take top N for detailed processing
        raw_players.sort(key=lambda p: p.get("total_points", 0), reverse=True)
        top_players = raw_players[:top_n]

        players = []
        for rp in top_players:
            player = self._process_player(rp, next_gw)
            if player:
                players.append(player)

        # Sort processed players by predicted points
        players.sort(key=lambda p: p["predictedPts"], reverse=True)

        # Build fixtures for next GW
        next_fixtures = self._build_fixtures(next_gw)

        # Injuries
        injuries = [p for p in players if p["injured"]]

        # Captain picks (top 5 by predicted points, excluding injured)
        captain_picks = [p for p in players if not p["injured"]][:5]

        # Differentials (< 15% ownership, decent form)
        differentials = sorted(
            [p for p in players if p["ownership"] < 15 and p["form"] >= 4.0],
            key=lambda p: p["predictedPts"],
            reverse=True,
        )[:10]

        return {
            "meta": {
                "currentGW": current_gw,
                "nextGW": next_gw,
                "totalPlayers": len(raw_players),
                "processedPlayers": len(players),
                "updatedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            },
            "players": players,
            "fixtures": next_fixtures,
            "injuries": injuries,
            "captainPicks": captain_picks,
            "differentials": differentials,
        }

    def _process_player(self, raw: dict, next_gw: int) -> dict | None:
        """Transform a raw player element into an enriched record."""
        team_id = raw.get("team")
        team_info = self._teams.get(team_id, {})
        team_name = team_info.get("name", "Unknown")
        team_short = team_info.get("short_name", "UNK")

        position = POSITIONS.get(raw.get("element_type"), "UNK")

        # Basic stats
        total_points = raw.get("total_points", 0)
        form = float(raw.get("form", 0))
        price = raw.get("now_cost", 0) / 10  # API gives price in 0.1m units
        ownership = float(raw.get("selected_by_percent", 0))
        minutes = raw.get("minutes", 0)
        goals = raw.get("goals_scored", 0)
        assists = raw.get("assists", 0)
        clean_sheets = raw.get("clean_sheets", 0)
        bonus = raw.get("bonus", 0)
        appearances = raw.get("starts", 0) or 1

        # Per-90 stats
        mins_90 = _safe_div(minutes, 90)
        goals_per_90 = _safe_div(goals, mins_90)
        assists_per_90 = _safe_div(assists, mins_90)
        bonus_avg = _safe_div(bonus, appearances)

        # xG and xA (from API)
        xg = float(raw.get("expected_goals", 0))
        xa = float(raw.get("expected_assists", 0))
        xg_per_90 = _safe_div(xg, mins_90)
        xa_per_90 = _safe_div(xa, mins_90)

        # ICT index
        ict = float(raw.get("ict_index", 0))

        # Recent gameweek scores (from form data — we'll enhance with history in detail fetch)
        # For the basic version, estimate last 5 from form * variance
        gw_scores = self._estimate_recent_scores(raw)

        # Minutes played in recent games
        recent_minutes = self._estimate_recent_minutes(raw)

        # Momentum
        momentum = _classify_momentum(gw_scores, form)

        # Injury status
        injury_status = raw.get("status", "a")  # a=available, d=doubtful, i=injured, etc.
        injured = injury_status in ("i", "d", "s", "u")
        injury_note = raw.get("news", "") or ""
        chance_playing = raw.get("chance_of_playing_next_round")

        # Next fixture
        next_fixture_str, next_fdr = self._get_next_fixture(team_id, next_gw)

        # European competition
        euro_comp = self.fetcher.get_european_competition(team_name)

        # Dream team count
        dream_team_count = raw.get("dreamteam_count", 0)

        return {
            "id": raw["id"],
            "name": f"{raw.get('first_name', '')} {raw.get('second_name', '')}".strip(),
            "webName": raw.get("web_name", ""),
            "team": team_short,
            "teamFull": team_name,
            "teamId": team_id,
            "position": position,
            "price": round(price, 1),
            "ownership": ownership,
            "form": form,
            "totalPoints": total_points,
            "last5": gw_scores[-5:] if len(gw_scores) >= 5 else gw_scores,
            "minutesPlayed": recent_minutes[-5:] if len(recent_minutes) >= 5 else recent_minutes,
            "xG": round(xg_per_90, 2),
            "xA": round(xa_per_90, 2),
            "xGTotal": round(xg, 2),
            "xATotal": round(xa, 2),
            "goalsScored": goals,
            "assists": assists,
            "cleanSheets": clean_sheets,
            "bonusAvg": round(bonus_avg, 1),
            "ict": round(ict, 1),
            "momentum": momentum,
            "injured": injured,
            "injuryNote": injury_note,
            "injuryStatus": injury_status,
            "chanceOfPlaying": chance_playing,
            "nextFixture": next_fixture_str,
            "fdr": next_fdr,
            "euroComp": euro_comp,
            "dreamTeamCount": dream_team_count,
            "selectedBy": f"{ownership}%",
            "minutes": minutes,
            "starts": raw.get("starts", 0),
            # predictedPts will be filled by the prediction model
            "predictedPts": 0.0,
        }

    def _estimate_recent_scores(self, raw: dict) -> list[float]:
        """
        Estimate recent GW scores from available data.
        In production, use get_player_detail() for exact history.
        """
        form = float(raw.get("form", 0))
        points_per_game = float(raw.get("points_per_game", 0))
        total = raw.get("total_points", 0)
        starts = raw.get("starts", 0) or 1

        # Generate approximate scores around the form value
        avg = points_per_game
        if form > 0:
            # Simulate some variance around form
            import random
            random.seed(raw["id"])  # deterministic per player
            scores = []
            for _ in range(5):
                noise = random.gauss(0, max(form * 0.3, 1.5))
                score = max(0, round(form + noise))
                scores.append(score)
            return scores
        return [round(avg)] * 5

    def _estimate_recent_minutes(self, raw: dict) -> list[int]:
        """Estimate recent minutes. Use player detail API for exact data."""
        total_mins = raw.get("minutes", 0)
        starts = raw.get("starts", 0) or 1
        avg_mins = min(90, round(total_mins / starts)) if starts > 0 else 0

        import random
        random.seed(raw["id"] + 1000)
        return [min(90, max(0, avg_mins + random.randint(-10, 5))) for _ in range(5)]

    def _get_next_fixture(self, team_id: int, next_gw: int) -> tuple[str, int]:
        """Get the next fixture string and FDR for a team."""
        gw_fixtures = self._fixtures_by_gw.get(next_gw, [])

        for f in gw_fixtures:
            if f["team_h"] == team_id:
                opponent = self._teams.get(f["team_a"], {})
                opp_short = opponent.get("short_name", "???")
                fdr = f.get("team_h_difficulty", 3)
                return f"{opp_short} (H)", fdr
            elif f["team_a"] == team_id:
                opponent = self._teams.get(f["team_h"], {})
                opp_short = opponent.get("short_name", "???")
                fdr = f.get("team_a_difficulty", 3)
                return f"{opp_short} (A)", fdr

        return "BGW", 0  # Blank gameweek

    def _build_fixtures(self, gameweek: int) -> list[dict]:
        """Build the fixture list for a gameweek."""
        gw_fixtures = self._fixtures_by_gw.get(gameweek, [])
        result = []

        for f in gw_fixtures:
            home_team = self._teams.get(f["team_h"], {})
            away_team = self._teams.get(f["team_a"], {})

            kickoff = f.get("kickoff_time", "")
            if kickoff:
                try:
                    dt = __import__("datetime").datetime.fromisoformat(
                        kickoff.replace("Z", "+00:00")
                    )
                    time_str = dt.strftime("%a %H:%M")
                except (ValueError, TypeError):
                    time_str = kickoff
            else:
                time_str = "TBD"

            home_euro = self.fetcher.get_european_competition(
                home_team.get("name", "")
            )
            away_euro = self.fetcher.get_european_competition(
                away_team.get("name", "")
            )

            result.append({
                "home": home_team.get("short_name", "???"),
                "homeFull": home_team.get("name", "???"),
                "away": away_team.get("short_name", "???"),
                "awayFull": away_team.get("name", "???"),
                "time": time_str,
                "homeFdr": f.get("team_h_difficulty", 3),
                "awayFdr": f.get("team_a_difficulty", 3),
                "homeEuro": home_euro,
                "awayEuro": away_euro,
                "finished": f.get("finished", False),
                "homeScore": f.get("team_h_score"),
                "awayScore": f.get("team_a_score"),
            })

        return result
