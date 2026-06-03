#!/usr/bin/env python3
"""
JARVIS First-Run Onboarding.

Interactive setup script that collects credentials, validates integrations,
and writes config.yaml + .env. Run once after cloning:

    python scripts/setup.py
"""
import json
import os
import sys
import asyncio
from pathlib import Path


# ── Terminal helpers ──────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM  = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def header(text: str) -> None:
    print(f"\n{CYAN}{BOLD}{'─' * 60}{RESET}")
    print(f"{CYAN}{BOLD}  {text}{RESET}")
    print(f"{CYAN}{BOLD}{'─' * 60}{RESET}\n")


def ok(text: str) -> None:
    print(f"  {GREEN}✓{RESET} {text}")


def warn(text: str) -> None:
    print(f"  {YELLOW}⚠{RESET}  {text}")


def fail(text: str) -> None:
    print(f"  {RED}✗{RESET} {text}")


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    display_default = f" [{DIM}{'*' * len(default) if secret and default else default}{RESET}]" if default else ""
    try:
        if secret:
            import getpass
            val = getpass.getpass(f"  {prompt}{display_default}: ")
        else:
            val = input(f"  {prompt}{display_default}: ").strip()
        return val or default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)


def confirm(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = ask(prompt + suffix)
    if not raw:
        return default
    return raw.lower().startswith("y")


# ── Validation helpers ────────────────────────────────────────────────────────

async def _check_anthropic(key: str) -> bool:
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=key)
        await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": "Hi"}],
        )
        return True
    except Exception:
        return False


async def _check_brave(key: str) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": key},
                params={"q": "test", "count": 1},
            )
            return r.status_code == 200
    except Exception:
        return False


async def _check_home_assistant(url: str, token: str) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{url.rstrip('/')}/api/",
                headers={"Authorization": f"Bearer {token}"},
            )
            return r.status_code == 200
    except Exception:
        return False


# ── Section collectors ────────────────────────────────────────────────────────

def collect_user_profile() -> dict:
    header("User Profile")
    name = ask("Your name")
    tz = ask("Timezone", default="America/New_York")
    wake_time = ask("Wake time (HH:MM)", default="07:00")
    preferred_name = ask("Preferred name (how JARVIS addresses you)", default=name.split()[0] if name else "")
    return {
        "name": name,
        "timezone": tz,
        "wake_time": wake_time,
        "preferred_name": preferred_name,
    }


def collect_core_keys(env: dict) -> None:
    header("Core API Keys")

    anthropic_key = ask("Anthropic API key", default=env.get("ANTHROPIC_API_KEY", ""), secret=True)
    if anthropic_key:
        env["ANTHROPIC_API_KEY"] = anthropic_key
        print("  Validating...", end="", flush=True)
        valid = asyncio.run(_check_anthropic(anthropic_key))
        print(f"\r  {'  ' if valid else '  '}", end="")
        ok("Anthropic API key valid") if valid else fail("Anthropic API key invalid — JARVIS requires this")

    api_secret = ask("JARVIS API secret (JWT password for remote access)", default=env.get("JARVIS_API_SECRET", ""), secret=True)
    if api_secret:
        env["JARVIS_API_SECRET"] = api_secret


def collect_voice_keys(env: dict) -> None:
    header("Voice (optional)")
    print(f"  {DIM}JARVIS works without voice — it'll fall back to text output.{RESET}\n")

    if confirm("Configure voice input (Deepgram STT)?", default=False):
        key = ask("Deepgram API key", default=env.get("DEEPGRAM_API_KEY", ""), secret=True)
        if key:
            env["DEEPGRAM_API_KEY"] = key

        key = ask("Picovoice access key (wake word)", default=env.get("PICOVOICE_ACCESS_KEY", ""), secret=True)
        if key:
            env["PICOVOICE_ACCESS_KEY"] = key

    if confirm("Configure voice output (Cartesia TTS)?", default=False):
        key = ask("Cartesia API key", default=env.get("CARTESIA_API_KEY", ""), secret=True)
        if key:
            env["CARTESIA_API_KEY"] = key
        voice_id = ask("Cartesia voice ID", default=env.get("CARTESIA_VOICE_ID", ""))
        if voice_id:
            env["CARTESIA_VOICE_ID"] = voice_id
    elif confirm("Use ElevenLabs instead?", default=False):
        key = ask("ElevenLabs API key", default=env.get("ELEVENLABS_API_KEY", ""), secret=True)
        if key:
            env["ELEVENLABS_API_KEY"] = key


def collect_search_key(env: dict) -> None:
    header("Web Search (optional)")
    print(f"  {DIM}Enables real-time research. Brave Search recommended.{RESET}\n")

    if confirm("Configure Brave Search?", default=False):
        key = ask("Brave Search API key", default=env.get("BRAVE_SEARCH_API_KEY", ""), secret=True)
        if key:
            env["BRAVE_SEARCH_API_KEY"] = key
            print("  Validating...", end="", flush=True)
            valid = asyncio.run(_check_brave(key))
            print(f"\r  {'  '}", end="")
            ok("Brave Search key valid") if valid else warn("Brave Search key may be invalid")


def collect_integrations(env: dict, config: dict) -> None:
    header("Integrations (optional)")
    print(f"  {DIM}All integrations are optional — add keys anytime in .env{RESET}\n")

    if confirm("Configure Home Assistant?", default=False):
        url = ask("Home Assistant URL", default=env.get("HOME_ASSISTANT_URL", "http://homeassistant.local:8123"))
        token = ask("Long-Lived Access Token", default=env.get("HOME_ASSISTANT_TOKEN", ""), secret=True)
        if url and token:
            env["HOME_ASSISTANT_URL"] = url
            env["HOME_ASSISTANT_TOKEN"] = token
            print("  Validating...", end="", flush=True)
            valid = asyncio.run(_check_home_assistant(url, token))
            print(f"\r  {'  '}", end="")
            ok("Home Assistant connected") if valid else warn("Could not reach Home Assistant")

    if confirm("Configure finance tracking (Alpaca)?", default=False):
        key = ask("Alpaca API key", default=env.get("ALPACA_API_KEY", ""), secret=True)
        secret = ask("Alpaca secret key", default=env.get("ALPACA_SECRET_KEY", ""), secret=True)
        paper = confirm("  Use paper trading endpoint?", default=True)
        if key:
            env["ALPACA_API_KEY"] = key
        if secret:
            env["ALPACA_SECRET_KEY"] = secret
        config["alpaca_paper"] = paper

        watchlist_raw = ask("Tickers to watch (comma-separated, e.g. AAPL,MSFT,SPY)")
        if watchlist_raw:
            config["watchlist"] = [t.strip().upper() for t in watchlist_raw.split(",") if t.strip()]

    if confirm("Configure health tracking (Oura Ring)?", default=False):
        token = ask("Oura personal access token", default=env.get("OURA_PERSONAL_TOKEN", ""), secret=True)
        if token:
            env["OURA_PERSONAL_TOKEN"] = token

    if confirm("Configure email (Gmail)?", default=False):
        creds_path = ask("Path to credentials.json", default=env.get("GOOGLE_CREDENTIALS_PATH", "~/.jarvis/credentials.json"))
        env["GOOGLE_CREDENTIALS_PATH"] = creds_path
        token_path = ask("Path to token.json", default=env.get("GOOGLE_TOKEN_PATH", "~/.jarvis/token.json"))
        env["GOOGLE_TOKEN_PATH"] = token_path


def collect_remote_api(env: dict, config: dict) -> None:
    header("Remote Access")
    print(f"  {DIM}Enables phone/browser access to JARVIS via HTTP + WebSocket.{RESET}\n")

    enable = confirm("Enable remote API?", default=True)
    config["enable_remote_api"] = enable

    if enable:
        port = ask("API port", default=str(config.get("api_port", 8765)))
        config["api_port"] = int(port)
        env["JARVIS_API_PORT"] = port


# ── Writers ───────────────────────────────────────────────────────────────────

def write_env(env: dict, path: Path) -> None:
    existing = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing.update({k: v for k, v in env.items() if v})
    lines = [f"{k}={v}" for k, v in sorted(existing.items())]
    path.write_text("\n".join(lines) + "\n")


def write_config(user: dict, config: dict, path: Path) -> None:
    import yaml

    defaults = {
        "user": user,
        "wake_word": "jarvis",
        "input_mode": "text",
        "enable_remote_api": True,
        "api_port": 8765,
        "notes_dir": "./data/notes",
        "active_projects": [],
        "anticipator": {
            "check_interval": 300,
            "urgent_interval": 60,
        },
        "memory": {
            "db_path": "./data/jarvis.db",
            "chroma_path": "./data/chroma",
            "max_working": 20,
        },
        "voice": {
            "provider": "cartesia",
        },
    }
    defaults.update(config)
    # Merge nested dicts rather than overwriting
    for k in ("anticipator", "memory", "voice"):
        if k in config and isinstance(config[k], dict):
            defaults[k] = {**defaults.get(k, {}), **config[k]}

    path.write_text(yaml.dump(defaults, default_flow_style=False, sort_keys=False))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    root = Path(__file__).parent.parent
    os.chdir(root)

    print(f"\n{CYAN}{BOLD}")
    print("  ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗")
    print("  ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝")
    print("  ██║███████║██████╔╝██║   ██║██║███████╗")
    print("  ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║")
    print("  ██║██║  ██║██║  ██║ ╚████╔╝ ██║███████║")
    print("  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝")
    print(f"{RESET}")
    print("  Personal AI Operating System — First Run Setup\n")
    print(f"  {DIM}Press Ctrl-C at any time to abort. You can re-run this script.{RESET}")
    print(f"  {DIM}API keys can be added/changed anytime in .env{RESET}")

    env_path = root / ".env"
    config_path = root / "config.yaml"

    # Load existing values
    existing_env: dict = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing_env[k.strip()] = v.strip()

    env: dict = {}
    config: dict = {}

    user = collect_user_profile()
    collect_core_keys(env)
    collect_voice_keys(env)
    collect_search_key(env)
    collect_integrations(env, config)
    collect_remote_api(env, config)

    header("Writing Configuration")

    Path("./data").mkdir(exist_ok=True)

    write_env({**existing_env, **env}, env_path)
    ok(f".env written → {env_path}")

    write_config(user, config, config_path)
    ok(f"config.yaml written → {config_path}")

    print(f"\n{GREEN}{BOLD}  Setup complete!{RESET}")
    print(f"\n  Start JARVIS:  {CYAN}python main.py{RESET}")
    if config.get("enable_remote_api", True):
        port = config.get("api_port", 8765)
        print(f"  Web UI:        {CYAN}http://localhost:{port}/ui{RESET}")
    print()


if __name__ == "__main__":
    # Guard: must run from repo root or scripts/
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("Run: pip install -r requirements.txt first")
        sys.exit(1)
    main()
