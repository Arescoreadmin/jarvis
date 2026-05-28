"""
Home Assistant integration. Controls lights, locks, thermostats, media, and any HA entity.
Also provides anomaly detection for the anticipator.
"""
import httpx
from tools.registry import ToolBase


class HomeControlTool(ToolBase):
    name = "home_control"
    description = (
        "Control smart home devices via Home Assistant. "
        "Turn lights on/off, set brightness/color, adjust thermostat, lock/unlock doors, "
        "control media players, check device states."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["turn_on", "turn_off", "toggle", "set", "get_state", "list_devices"],
                "description": "The action to perform",
            },
            "entity_id": {
                "type": "string",
                "description": "Home Assistant entity ID (e.g. light.living_room, lock.front_door)",
            },
            "attributes": {
                "type": "object",
                "description": "Optional attributes: brightness (0-255), color_temp, rgb_color, temperature, media_content_id",
            },
        },
        "required": ["action"],
    }

    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/") if url else ""
        self._token = token

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def run(self, action: str, entity_id: str = "", attributes: dict | None = None) -> str:
        if not self._url or not self._token:
            return "[home_control] Home Assistant not configured (HOME_ASSISTANT_URL / HOME_ASSISTANT_TOKEN missing)"

        if action == "list_devices":
            return await self._list_devices()
        if action == "get_state":
            return await self._get_state(entity_id)
        return await self._call_service(action, entity_id, attributes or {})

    async def _call_service(self, action: str, entity_id: str, attributes: dict) -> str:
        domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
        payload = {"entity_id": entity_id, **attributes}
        service = action.replace("turn_on", "turn_on").replace("turn_off", "turn_off")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._url}/api/services/{domain}/{service}",
                headers=self._headers,
                json=payload,
            )
            if resp.status_code == 200:
                return f"Done: {action} {entity_id}"
            return f"Error {resp.status_code}: {resp.text[:200]}"

    async def _get_state(self, entity_id: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._url}/api/states/{entity_id}",
                headers=self._headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state", "unknown")
                attrs = data.get("attributes", {})
                return f"{entity_id}: {state} | {attrs}"
            return f"Error {resp.status_code}"

    async def _list_devices(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._url}/api/states", headers=self._headers)
            if resp.status_code == 200:
                states = resp.json()
                summary = []
                for s in states[:50]:
                    summary.append(f"{s['entity_id']}: {s['state']}")
                return "\n".join(summary)
            return f"Error {resp.status_code}"

    async def get_anomalies(self) -> list[dict]:
        """Called by anticipator to detect home anomalies."""
        if not self._url or not self._token:
            return []
        alerts = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._url}/api/states", headers=self._headers)
                if resp.status_code != 200:
                    return []
                states = resp.json()

            for s in states:
                entity = s.get("entity_id", "")
                state = s.get("state", "")
                attrs = s.get("attributes", {})

                if entity.startswith("lock.") and state == "unlocked":
                    if attrs.get("changed_by") != "user":
                        alerts.append({
                            "device": entity,
                            "priority": "high",
                            "message": f"{entity} is unlocked",
                            "action": f"Lock {entity}?",
                        })

                if entity.startswith("binary_sensor.") and "door" in entity and state == "on":
                    alerts.append({
                        "device": entity,
                        "priority": "medium",
                        "message": f"{entity.replace('binary_sensor.', '')} is open",
                        "action": "",
                    })

                if entity.startswith("climate."):
                    temp = attrs.get("current_temperature", 0)
                    if isinstance(temp, (int, float)) and (temp > 85 or temp < 45):
                        alerts.append({
                            "device": entity,
                            "priority": "high",
                            "message": f"Unusual temperature: {temp}°F on {entity}",
                            "action": "Check thermostat",
                        })

        except Exception:
            pass
        return alerts
