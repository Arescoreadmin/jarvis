"""
TTS + audio output.

Primary: Cartesia (~80ms TTFB, streaming)
Fallback: ElevenLabs
Fallback: pyttsx3 (offline, no API key needed)

Mode-aware: CRISIS mode uses a flatter, more clipped voice style.
"""
import asyncio
import logging
import os
from typing import AsyncIterator, Optional

log = logging.getLogger("voice")

try:
    import cartesia
    CARTESIA_AVAILABLE = True
except ImportError:
    CARTESIA_AVAILABLE = False

try:
    from elevenlabs import ElevenLabs, VoiceSettings
    ELEVENLABS_AVAILABLE = True
except ImportError:
    ELEVENLABS_AVAILABLE = False

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False


VOICE_IDS = {
    # Cartesia voice IDs — replace with your preferred cloned voice
    "default": os.environ.get("CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091"),
    "crisis": os.environ.get("CARTESIA_VOICE_ID_CRISIS", "a0e99841-438c-4a64-b679-ae501e7d6091"),
}

ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")


class Voice:
    def __init__(self):
        self._cartesia: Optional[object] = None
        self._elevenlabs: Optional[object] = None
        self._pyttsx: Optional[object] = None
        self._pa: Optional[object] = None
        self._mode = "default"
        self._init()

    def _init(self) -> None:
        if CARTESIA_AVAILABLE and os.environ.get("CARTESIA_API_KEY"):
            try:
                self._cartesia = cartesia.Cartesia(api_key=os.environ["CARTESIA_API_KEY"])
                log.info("Voice: Cartesia initialized")
                return
            except Exception as e:
                log.warning("Cartesia init failed: %s", e)

        if ELEVENLABS_AVAILABLE and os.environ.get("ELEVENLABS_API_KEY"):
            try:
                self._elevenlabs = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
                log.info("Voice: ElevenLabs initialized")
                return
            except Exception as e:
                log.warning("ElevenLabs init failed: %s", e)

        if PYTTSX3_AVAILABLE:
            try:
                self._pyttsx = pyttsx3.init()
                self._pyttsx.setProperty("rate", 175)
                log.info("Voice: pyttsx3 initialized (offline fallback)")
            except Exception as e:
                log.warning("pyttsx3 init failed: %s", e)

    def set_mode(self, mode: str) -> None:
        self._mode = "crisis" if mode == "crisis" else "default"

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        if self._cartesia:
            await self._speak_cartesia(text)
        elif self._elevenlabs:
            await self._speak_elevenlabs(text)
        elif self._pyttsx:
            await self._speak_pyttsx(text)
        else:
            print(f"JARVIS: {text}")

    async def speak_stream(self, text_iter: AsyncIterator[str]) -> None:
        """Stream tokens and speak as sentences complete."""
        buffer = ""
        sentence_endings = {".", "!", "?", "—"}

        async for chunk in text_iter:
            buffer += chunk
            if any(buffer.rstrip().endswith(end) for end in sentence_endings):
                sentence = buffer.strip()
                if sentence:
                    await self.speak(sentence)
                buffer = ""

        if buffer.strip():
            await self.speak(buffer.strip())

    async def _speak_cartesia(self, text: str) -> None:
        voice_id = VOICE_IDS.get(self._mode, VOICE_IDS["default"])
        try:
            if PYAUDIO_AVAILABLE:
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=44100,
                    output=True,
                )
                for chunk in self._cartesia.tts.bytes(
                    model_id="sonic-english",
                    transcript=text,
                    voice_id=voice_id,
                    output_format={"container": "raw", "encoding": "pcm_f32le", "sample_rate": 44100},
                ):
                    stream.write(chunk)
                stream.stop_stream()
                stream.close()
                pa.terminate()
            else:
                audio_chunks = b""
                for chunk in self._cartesia.tts.bytes(
                    model_id="sonic-english",
                    transcript=text,
                    voice_id=voice_id,
                    output_format={"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
                ):
                    audio_chunks += chunk
                await asyncio.get_event_loop().run_in_executor(None, self._play_bytes, audio_chunks)
        except Exception as e:
            log.error("Cartesia TTS failed: %s", e)
            await self._speak_fallback(text)

    async def _speak_elevenlabs(self, text: str) -> None:
        try:
            audio = self._elevenlabs.generate(
                text=text,
                voice=ELEVENLABS_VOICE_ID,
                voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.8),
            )
            audio_bytes = b"".join(audio)
            await asyncio.get_event_loop().run_in_executor(None, self._play_bytes, audio_bytes)
        except Exception as e:
            log.error("ElevenLabs TTS failed: %s", e)
            await self._speak_fallback(text)

    async def _speak_pyttsx(self, text: str) -> None:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: (self._pyttsx.say(text), self._pyttsx.runAndWait())
            )
        except Exception as e:
            log.error("pyttsx3 TTS failed: %s", e)
            print(f"JARVIS: {text}")

    async def _speak_fallback(self, text: str) -> None:
        if self._pyttsx:
            await self._speak_pyttsx(text)
        else:
            print(f"JARVIS: {text}")

    def _play_bytes(self, audio_bytes: bytes) -> None:
        try:
            import simpleaudio as sa
            play_obj = sa.play_buffer(audio_bytes, 1, 2, 44100)
            play_obj.wait_done()
        except ImportError:
            try:
                import subprocess
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio_bytes)
                    subprocess.run(["aplay", f.name], check=True, capture_output=True)
            except Exception:
                pass
