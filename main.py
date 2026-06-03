"""
JARVIS entry point.

Startup sequence:
  1. Load config + env
  2. Init memory, mode manager
  3. Build tool registry
  4. Init brain (Claude client)
  5. Init context aggregator
  6. Start anticipator (background)
  7. Start remote API (background, if enabled)
  8. Start main interaction loop (voice + text)
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("./data/jarvis.log"),
    ],
)
log = logging.getLogger("jarvis")

from core.memory import Memory
from core.modes import ModeManager, Mode
from core.brain import Brain
from core.context import ContextAggregator
from core.anticipator import Anticipator
from core.distiller import Distiller
from core.listener import Listener
from core.voice import Voice
from tools.registry import build_registry
from agents.executor import Executor
from api.server import app as api_app, register_components


def load_config() -> dict:
    config_path = Path("./config.yaml")
    if not config_path.exists():
        log.warning("config.yaml not found — using defaults")
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


async def on_alert(alert) -> None:
    """Anticipator callback — surfaces proactive alerts to the user."""
    log.info("Alert [%s] %s: %s", alert.priority, alert.category, alert.message)
    # In voice mode: speak the alert
    # In text mode: print it
    prefix = {
        "urgent": "⚠ URGENT",
        "high": "▶ Note",
        "medium": "·",
        "low": "",
    }.get(alert.priority, "")
    print(f"\n{prefix} {alert.message}")
    if alert.action_hint:
        print(f"  → {alert.action_hint}")


async def morning_brief(brain, anticipator, voice, modes) -> None:
    import datetime
    now = datetime.datetime.now()
    if 5 <= now.hour < 10:
        alerts = anticipator.morning_brief()
        if alerts:
            brief_lines = [f"[{a.priority.upper()}] {a.message}" for a in alerts[:5]]
            brief_text = "Morning brief:\n" + "\n".join(brief_lines)
            response_iter = await brain.respond(brief_text)
            await voice.speak_stream(response_iter)
        else:
            await voice.speak("Morning. Nothing urgent.")


async def run_nightly_distiller(distiller) -> None:
    while True:
        await asyncio.sleep(1800)  # check every 30 minutes
        if distiller.should_run_tonight():
            log.info("Running nightly distillation")
            try:
                result = await distiller.run()
                log.info("Distillation: %s", result[:120])
            except Exception as e:
                log.error("Distillation failed: %s", e)


async def run_remote_api(port: int) -> None:
    import uvicorn
    config = uvicorn.Config(api_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    config = load_config()
    Path("./data").mkdir(exist_ok=True)

    memory = Memory()
    modes = ModeManager(memory)

    stored_mode = memory.get_semantic("active_mode")
    if stored_mode:
        try:
            modes.set(Mode(stored_mode))
        except ValueError:
            pass

    registry = build_registry(config)
    brain = Brain(memory, modes, None, registry)  # context injected below
    context = ContextAggregator(memory, modes, registry)
    brain._context = context

    anticipator = Anticipator(
        memory=memory,
        mode_manager=modes,
        context_aggregator=context,
        tool_registry=registry,
        on_alert=on_alert,
    )

    register_components(brain, memory, context, modes, anticipator, registry)

    executor = Executor(memory, modes, context, registry)
    voice = Voice()
    listener = Listener(wake_word=config.get("wake_word", "jarvis"))

    distiller = Distiller(memory)

    anticipator.start()
    log.info("Anticipator started")

    api_port = int(os.environ.get("JARVIS_API_PORT", config.get("api_port", 8765)))
    if config.get("enable_remote_api", True):
        asyncio.create_task(run_remote_api(api_port))
        log.info("Remote API starting on port %d", api_port)

    asyncio.create_task(run_nightly_distiller(distiller))

    profile = memory.get_user_profile()
    name = profile.get("preferred_name") or profile.get("name") or ""
    greeting = f"JARVIS online. {name + ', good to go.' if name else 'Ready.'}"
    print(greeting)

    await morning_brief(brain, anticipator, voice, modes)

    async for utterance in listener.stream():
        utterance = utterance.strip()
        if not utterance:
            continue

        lower = utterance.lower()

        # Mode switching
        if lower.startswith("mode ") or lower.startswith("switch to "):
            mode_str = lower.replace("mode ", "").replace("switch to ", "").strip()
            result = modes.set(modes.from_string(mode_str))
            voice.set_mode(modes.current.value)
            await voice.speak(result)
            continue

        # Watchlist — "watch <tool>: <description> when <condition>"
        if lower.startswith("watch ") and ":" in utterance:
            # Format: "watch <tool_name>: <description> when <condition>"
            rest = utterance[6:].strip()
            tool_part, _, desc_cond = rest.partition(":")
            tool_name = tool_part.strip()
            if " when " in desc_cond:
                description, _, condition = desc_cond.strip().partition(" when ")
            else:
                description = desc_cond.strip()
                condition = "anything notable changes"
            watch_id = memory.watchlist.add(
                description=description.strip(),
                tool_name=tool_name.strip(),
                tool_args={},
                condition=condition.strip(),
            )
            await voice.speak(f"Watching '{description.strip()}'. I'll alert you when the condition is met.")
            continue

        if lower.startswith("stop watching "):
            query = utterance[14:].strip()
            watches = memory.watchlist.get_all()
            removed = [w for w in watches if query.lower() in w["description"].lower()]
            for w in removed:
                memory.watchlist.remove(w["id"])
            if removed:
                await voice.speak(f"Stopped watching {len(removed)} item(s).")
            else:
                await voice.speak("No matching watches found.")
            continue

        if lower in ("distill memory", "distill", "run distillation"):
            result = await distiller.run(force=True)
            await voice.speak(result[:200])
            continue

        # Procedure learning
        if lower.startswith("remember that ") or lower.startswith("when i say "):
            # "when i say 'prep deck' I mean update slides and brief the team"
            parts = utterance.split(" I mean ", 1)
            if len(parts) == 2:
                trigger_raw = parts[0].replace("when i say ", "").replace("remember that ", "").strip("'\" ")
                expansion = parts[1].strip()
                memory.add_procedure(trigger_raw, expansion)
                await voice.speak(f"Got it. '{trigger_raw}' → {expansion}")
                continue

        # Check for procedural expansion
        expanded = memory.expand_procedure(lower)
        if expanded:
            utterance = expanded

        # Explicit execution trigger
        if lower.startswith(("execute ", "run plan ", "do this: ", "do: ")):
            for prefix in ("execute ", "run plan ", "do this: ", "do: "):
                if lower.startswith(prefix):
                    goal = utterance[len(prefix):].strip()
                    break
            log.debug("Executing goal: %s", goal[:80])
            try:
                async def _exec_stream():
                    async for chunk in executor.run(goal):
                        yield chunk
                await voice.speak_stream(_exec_stream())
            except Exception as e:
                log.error("Execution error: %s", e)
                await voice.speak("Execution failed. Check logs.")
            continue

        log.debug("Processing: %s", utterance[:80])

        try:
            response_iter = await brain.respond(utterance)
            await voice.speak_stream(response_iter)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("Response error: %s", e)
            await voice.speak("Error processing that. Check logs.")

    log.info("Shutting down")
    anticipator.stop()
    memory.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nJARVIS offline.")
