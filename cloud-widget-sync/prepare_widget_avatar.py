import json
import os
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url=url, method="GET")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_avatar_url(username: str, token: str) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "discord-widget-cloud-sync",
    }
    body = get_json(f"https://api.github.com/users/{username}", headers)
    avatar_url = str(body.get("avatar_url", "")).strip()
    if not avatar_url:
        raise RuntimeError("GitHub profile avatar_url was empty")
    return avatar_url


def download_image(url: str) -> Image.Image:
    req = urllib.request.Request(url, headers={"User-Agent": "discord-widget-cloud-sync"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return Image.open(BytesIO(resp.read())).convert("RGBA")


def build_mask(size: int, top_strip: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(mask)
    if top_strip > 0:
        draw.rectangle((0, 0, size, top_strip), fill=0)
    if radius > 0:
        draw.rectangle((size - radius, 0, size, radius), fill=0)
        draw.pieslice((size - 2 * radius, 0, size, 2 * radius), 270, 360, fill=255)
    return mask


def main() -> int:
    base = Path(__file__).resolve().parent
    config_file_name = os.getenv("CONFIG_PATH", "config.public.json")
    config_path = (base / config_file_name) if not Path(config_file_name).is_absolute() else Path(config_file_name)
    config = read_json(config_path)

    github = config.get("github", {})
    widget = config.get("widget", {})
    avatar_cfg = config.get("avatar_processing", {})

    username = os.getenv("GITHUB_USERNAME", "").strip() or str(github.get("username", "")).strip()
    if not username:
        raise RuntimeError("Missing GitHub username")

    gh_env = github.get("token_env", "GH_PAT")
    gh_token = os.getenv(gh_env, "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
    if not gh_token:
        raise RuntimeError(f"Missing GitHub token in env var {gh_env} (or GITHUB_TOKEN)")

    avatar_url = fetch_avatar_url(username, gh_token)
    image = download_image(avatar_url)

    size = int(avatar_cfg.get("size", 512))
    top_strip = int(avatar_cfg.get("top_strip", 17))
    radius = int(avatar_cfg.get("top_right_radius", 36))
    output_path = str(avatar_cfg.get("output_path", "generated/github-avatar-fixed.png"))

    fitted = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
    mask = build_mask(size, top_strip, radius)
    fitted.putalpha(mask)

    out_file = base / output_path
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fitted.save(out_file, format="PNG", optimize=True)
    print(f"Wrote processed avatar to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())