# Peekorobo Discord bot

Slash commands that call the [Peekorobo](https://www.peekorobo.com) HTTP API: FRC team and event stats, rankings, matches, awards, and more. Replies use embeds, pagination buttons on long results, and links to `peekorobo.com/team/...` and `peekorobo.com/event/...`.

## Slash commands

| Command | Purpose |
|---------|---------|
| `/peek_ping` | Check API reachability and API key. |
| `/peek_team` | Team profile, season ACE/RAW, registered events per season, `event_perf` lines, optional year filter. |
| `/peek_event` | One event’s general info (dates, location, webcast, etc.). |
| `/peek_rankings` | Event rankings (paginated). |
| `/peek_events` | Events for a season (filters + pagination). |
| `/peek_event_keys` | Event keys for a year (paginated). |
| `/peek_team_awards` | Awards for a team (paginated). |
| `/peek_team_events` | Event keys for a team (paginated). |
| `/peek_event_teams` | Team list for an event (paginated). |
| `/peek_event_matches` | Matches at an event, newest first (optional team filter; paginated). |
| `/peek_event_awards` | Awards at an event (optional team filter; paginated). |
| `/peek_event_perfs` | Per-team metrics at an event; optional `team_key` (`254` or `frc254`) for one team (paginated). |

Long outputs use **◀ / page / ▶** buttons; only the user who ran the command can use them.
