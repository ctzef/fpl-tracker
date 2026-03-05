"""
FPL Data Fetcher
Pulls all player, team, fixture, and gameweek data from the official FPL API.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger(__name__)

BASE_URL = "https://fantasy.premierleague.com/api"

ENDPOINTS = {
    "bootstrap": f"{BASE_URL}/bootstrap-static/",
    "fixtures": f"{BASE_URL}/fixtures/",
    "player_detail": f"{BASE_URL}/element-summary/{{player_id}}/",
    "gameweek_live": f"{BASE_URL}/event/{{gw}}/live/",
}

# Teams that play in European competitions (update each season)
EUROPEAN_TEAMS = {
    "UCL": ["Liverpool", "Arsenal", "Aston Villa", "Man City"],
    "UEL": ["Man Utd", "Tottenham"],
    "UECL": ["Chelsea"],
}


def _fetch_json(url: str, timeout: int = 30) -> dict | list:
    """Fetch JSON from a URL using only stdlib."""
    req = Request(url, headers={"User-Agent": "FPL-Tracker/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        logger.error("Failed to fetch %s: %s", url, e)
        raise


class FPLFetcher:
    """Fetches and caches FPL data for a single session."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap: dict | None = None
        self._fixtures: list | None = None

    # ─── Core data ────────────────────────────────────────────────

    def get_bootstrap(self) -> dict:
        """Main FPL data dump: players, teams, gameweeks, etc."""
        if self._bootstrap is None:
            logger.info("Fetching bootstrap-static data...")
            self._bootstrap = _fetch_json(ENDPOINTS["bootstrap"])
            self._save("bootstrap.json", self._bootstrap)
        return self._bootstrap

    def get_fixtures(self) -> list:
        """All fixtures for the season."""
        if self._fixtures is None:
            logger.info("Fetching fixtures...")
            self._fixtures = _fetch_json(ENDPOINTS["fixtures"])
            self._save("fixtures.json", self._fixtures)
        return self._fixtures

    def get_player_detail(self, player_id: int) -> dict:
        """Per-player history and upcoming fixtures."""
        url = ENDPOINTS["player_detail"].format(player_id=player_id)
        logger.info("Fetching detail for player %d...", player_id)
        data = _fetch_json(url)
        self._save(f"player_{player_id}.json", data)
        return data

    def get_gameweek_live(self, gw: int) -> dict:
        """Live gameweek stats."""
        url = ENDPOINTS["gameweek_live"].format(gw=gw)
        logger.info("Fetching live data for GW%d...", gw)
        data = _fetch_json(url)
        self._save(f"gw_{gw}_live.json", data)
        return data

    # ─── Derived helpers ──────────────────────────────────────────

    def get_teams(self) -> dict[int, dict]:
        """Map of team_id -> team info."""
        boot = self.get_bootstrap()
        return {t["id"]: t for t in boot["teams"]}

    def get_current_gameweek(self) -> int:
        """Return the current (or next upcoming) gameweek number."""
        boot = self.get_bootstrap()
        for event in boot["events"]:
            if event["is_current"]:
                return event["id"]
            if event["is_next"]:
                return event["id"]
        # Fallback: last event
        return boot["events"][-1]["id"]

    def get_next_gameweek(self) -> int:
        """Return the next gameweek number."""
        boot = self.get_bootstrap()
        for event in boot["events"]:
            if event["is_next"]:
                return event["id"]
        current = self.get_current_gameweek()
        return min(current + 1, 38)

    def get_players_raw(self) -> list[dict]:
        """All player elements from the bootstrap."""
        return self.get_bootstrap()["elements"]

    def get_upcoming_fixtures(self, gameweek: int | None = None) -> list[dict]:
        """Fixtures for a specific gameweek (defaults to next)."""
        gw = gameweek or self.get_next_gameweek()
        return [f for f in self.get_fixtures() if f["event"] == gw]

    def get_european_competition(self, team_name: str) -> str | None:
        """Check if a team is in a European competition."""
        for comp, teams in EUROPEAN_TEAMS.items():
            if team_name in teams:
                return comp
        return None

    # ─── Persistence ──────────────────────────────────────────────

    def _save(self, filename: str, data: Any) -> None:
        """Save raw API data to disk for debugging / caching."""
        path = self.data_dir / filename
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug("Saved %s", path)

    def load_cached(self, filename: str) -> Any | None:
        """Load previously cached data if it exists."""
        path = self.data_dir / filename
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None
