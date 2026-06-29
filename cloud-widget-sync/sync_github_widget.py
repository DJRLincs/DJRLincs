import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def post_json(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str, headers: dict) -> tuple[dict, dict]:
    req = urllib.request.Request(url=url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body, dict(resp.headers)


def github_graphql(token: str, query: str, variables: dict) -> dict:
    url = "https://api.github.com/graphql"
    payload = {"query": query, "variables": variables}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "discord-widget-cloud-sync",
    }
    response = post_json(url, payload, headers)
    if "errors" in response:
        raise RuntimeError("GitHub GraphQL error: " + json.dumps(response["errors"]))
    return response["data"]


def github_rest_count_user_repos(username: str, token: str) -> int:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "discord-widget-cloud-sync",
    }

    total = 0
    page = 1
    while True:
        url = f"https://api.github.com/users/{username}/repos?per_page=100&page={page}&type=owner&sort=updated"
        repos, _ = get_json(url, headers)
        if not repos:
            break
        total += len(repos)
        page += 1
    return total


def github_rest_get_user_profile(username: str, token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "discord-widget-cloud-sync",
    }
    url = f"https://api.github.com/users/{username}"
    body, _ = get_json(url, headers)
    return body


def fetch_contributions_lifetime(username: str, token: str) -> int:
        years_query = """
        query($login: String!) {
            user(login: $login) {
                contributionsCollection {
                    contributionYears
                }
            }
        }
        """

        data = github_graphql(token, years_query, {"login": username})
        years = data["user"]["contributionsCollection"].get("contributionYears") or []
        if not years:
                return 0

        total = 0
        year_query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
            user(login: $login) {
                contributionsCollection(from: $from, to: $to) {
                    contributionCalendar {
                        totalContributions
                    }
                }
            }
        }
        """

        for year in years:
                from_dt = dt.datetime(int(year), 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
                to_dt = dt.datetime(int(year), 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)
                year_data = github_graphql(
                        token,
                        year_query,
                        {
                                "login": username,
                                "from": from_dt.isoformat().replace("+00:00", "Z"),
                                "to": to_dt.isoformat().replace("+00:00", "Z"),
                        },
                )
                total += int(year_data["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"])

        return total


def resolve_total_repos(config: dict, token: str) -> int:
    mode = (config["github"].get("repos_mode") or "user").strip().lower()
    tracked = [x for x in config["github"].get("tracked_repositories", []) if isinstance(x, str) and "/" in x]

    if mode == "tracked":
        return len(tracked)

    username = config["github"]["username"]
    return github_rest_count_user_repos(username, token)


def build_payload(config: dict, contributions: int, total_repos: int, avatar_url: str) -> dict:
    rig = config["rig_snapshot"]
    widget = config.get("widget", {})
    sync_tz = widget.get("sync_timezone_label", "UTC")
    now = dt.datetime.now(dt.timezone.utc)
    last_sync = now.strftime("%Y-%m-%d %H:%M") + f" {sync_tz}"

    dynamic = [
        {"type": 3, "name": "gh_avatar_url", "value": {"url": avatar_url}},
        {"type": 1, "name": "rig_cpu", "value": rig["cpu"]},
        {"type": 1, "name": "rig_gpu", "value": rig["gpu"]},
        {"type": 1, "name": "rig_ram", "value": rig["ram"]},
        {"type": 1, "name": "gh_contributions_year", "value": str(contributions)},
        {"type": 1, "name": "gh_total_repos", "value": str(total_repos)},
        {"type": 1, "name": "last_sync", "value": last_sync},
    ]

    return {
        "username": widget.get("username", "Dev Rig Snapshot"),
        "data": {
            "dynamic": dynamic,
        },
    }


def patch_discord_identity(config: dict, payload: dict) -> tuple[bool, int, str]:
    discord = config["discord"]
    app_id = os.getenv("DISCORD_APP_ID", "").strip() or str(discord.get("app_id", "")).strip()
    user_id = os.getenv("DISCORD_USER_ID", "").strip() or str(discord.get("user_id", "")).strip()
    identity_id_env = os.getenv("DISCORD_IDENTITY_ID", "").strip()
    identity_id = int(identity_id_env) if identity_id_env else int(discord.get("identity_id", 0))

    if not app_id or not user_id:
        raise RuntimeError("Missing Discord app/user id (set config or DISCORD_APP_ID / DISCORD_USER_ID)")

    token_env = discord.get("bot_token_env", "DISCORD_BOT_TOKEN")
    bot_token = os.getenv(token_env, "").strip()
    if not bot_token:
        raise RuntimeError(f"Missing Discord bot token in env var {token_env}")

    url = f"https://discord.com/api/v9/applications/{app_id}/users/{user_id}/identities/{identity_id}/profile"

    req = urllib.request.Request(url=url, data=json.dumps(payload).encode("utf-8"), method="PATCH")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bot {bot_token}")
    req.add_header("User-Agent", "DiscordBot (https://github.com/discord/discord-api-docs, 1.0.0)")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return 200 <= resp.status < 300, resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return False, e.code, body
    except Exception as e:
        return False, 0, str(e)


def validate_config(config: dict) -> None:
    app_id = os.getenv("DISCORD_APP_ID", "").strip() or str(config.get("discord", {}).get("app_id", "")).strip()
    user_id = os.getenv("DISCORD_USER_ID", "").strip() or str(config.get("discord", {}).get("user_id", "")).strip()
    if not app_id:
        raise ValueError("Missing discord.app_id (or DISCORD_APP_ID)")
    if not user_id:
        raise ValueError("Missing discord.user_id (or DISCORD_USER_ID)")

    username = os.getenv("GITHUB_USERNAME", "").strip() or str(config.get("github", {}).get("username", "")).strip()
    if not username:
        raise ValueError("Missing github.username (or GITHUB_USERNAME)")

    required_rig = ["cpu", "gpu", "ram"]
    for k in required_rig:
        if not str(config.get("rig_snapshot", {}).get(k, "")).strip():
            raise ValueError(f"Missing rig_snapshot.{k}")


def main() -> int:
    base = Path(__file__).resolve().parent
    config_file_name = os.getenv("CONFIG_PATH", "config.json")
    config_path = (base / config_file_name) if not Path(config_file_name).is_absolute() else Path(config_file_name)
    if not config_path.exists():
        print(f"Missing config file: {config_path}")
        return 1

    config = read_json(config_path)
    validate_config(config)

    gh_env = config["github"].get("token_env", "GH_PAT")
    gh_token = os.getenv(gh_env, "").strip()
    if not gh_token:
        gh_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not gh_token:
        raise RuntimeError(f"Missing GitHub token in env var {gh_env} (or GITHUB_TOKEN)")

    username = os.getenv("GITHUB_USERNAME", "").strip() or config["github"]["username"]
    profile = github_rest_get_user_profile(username, gh_token)
    avatar_url = str(profile.get("avatar_url", "")).strip()
    if not avatar_url:
        raise RuntimeError("GitHub profile avatar_url was empty")

    contributions = fetch_contributions_lifetime(username, gh_token)
    total_repos = resolve_total_repos(config, gh_token)

    payload = build_payload(config, contributions, total_repos, avatar_url)
    ok, status, body = patch_discord_identity(config, payload)

    if ok:
        print(f"Widget sync OK. contributions={contributions} total_repos={total_repos} status={status}")
        return 0

    print(f"Widget sync FAILED. status={status} body={body}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted")
        raise SystemExit(130)
