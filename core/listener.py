"""
Wake word detection + streaming STT pipeline.

Flow:
  Porcupine (always-on, on-device) → wake word detected
  → Deepgram streaming STT → utterance text
  → yield to main loop

Falls back gracefully if Porcupine or Deepgram is unavailable.
"""
import asyncio
import logging
import os
import struct
import wave
from io import BytesIO
from typing import AsyncIterator, Optional

log = logging.getLogger("listener")

try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    log.warning("pvporcupine not installed — wake word detection disabled")

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    log.warning("pyaudio not installed — microphone input disabled")

try:
    from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
    DEEPGRAM_AVAILABLE = True
except ImportError:
    DEEPGRAM_AVAILABLE = False
    log.warning("deepgram-sdk not installed — STT disabled")


SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_SIZE = 512
SILENCE_THRESHOLD = 500
SILENCE_DURATION = 1.5  # seconds of silence to end utterance


class Listener:
    def __init__(self, wake_word: str = "jarvis"):
        self._wake_word = wake_word
        self._porcupine = None
        self._pa = None
        self._stream = None
        self._deepgram = None
        self._active = False

    def _init_porcupine(self) -> bool:
        if not PORCUPINE_AVAILABLE:
            return False
        access_key = os.environ.get("PICOVOICE_ACCESS_KEY", "")
        if not access_key:
            log.warning("PICOVOICE_ACCESS_KEY not set — wake word detection disabled")
            return False
        try:
            self._porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=[self._wake_word],
            )
            return True
        except Exception as e:
            log.error("Porcupine init failed: %s", e)
            return False

    def _init_audio(self) -> bool:
        if not PYAUDIO_AVAILABLE:
            return False
        try:
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                rate=SAMPLE_RATE,
                channels=CHANNELS,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=FRAME_SIZE,
            )
            return True
        except Exception as e:
            log.error("Audio init failed: %s", e)
            return False

    async def stream(self) -> AsyncIterator[str]:
        """
        Continuously listen for the wake word then transcribe utterances.
        Yields each complete utterance as a string.
        """
        has_wake_word = self._init_porcupine()
        has_audio = self._init_audio()

        if not has_audio:
            log.info("No audio device — falling back to stdin input")
            async for utterance in self._stdin_stream():
                yield utterance
            return

        self._active = True
        log.info("JARVIS listening for wake word: '%s'", self._wake_word)

        try:
            while self._active:
                if has_wake_word:
                    detected = await asyncio.get_event_loop().run_in_executor(
                        None, self._wait_for_wake_word
                    )
                    if not detected:
                        continue
                    log.debug("Wake word detected")

                utterance = await self._capture_utterance()
                if utterance and utterance.strip():
                    yield utterance.strip()

        except asyncio.CancelledError:
            pass
        finally:
            self._cleanup()

    def _wait_for_wake_word(self) -> bool:
        if not self._stream or not self._porcupine:
            return True  # no wake word — always ready
        try:
            while True:
                pcm = self._stream.read(self._porcupine.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * self._porcupine.frame_length, pcm)
                result = self._porcupine.process(pcm)
                if result >= 0:
                    return True
        except Exception as e:
            log.error("Wake word detection error: %s", e)
            return False

    async def _capture_utterance(self) -> str:
        if DEEPGRAM_AVAILABLE and os.environ.get("DEEPGRAM_API_KEY"):
            return await self._transcribe_deepgram()
        return await self._transcribe_local()

    async def _transcribe_deepgram(self) -> str:
        api_key = os.environ.get("DEEPGRAM_API_KEY", "")
        transcript_parts = []
        done = asyncio.Event()

        try:
            dg = DeepgramClient(api_key)
            conn = dg.listen.asynclive.v("1")

            async def on_message(self_inner, result, **kwargs):
                sentence = result.channel.alternatives[0].transcript
                if result.is_final and sentence:
                    transcript_parts.append(sentence)

            async def on_speech_final(self_inner, result, **kwargs):
                done.set()

            conn.on(LiveTranscriptionEvents.Transcript, on_message)
            conn.on(LiveTranscriptionEvents.SpeechFinal, on_speech_final)

            options = LiveOptions(
                model="nova-2",
                language="en-US",
                smart_format=True,
                endpointing=500,
            )

            await conn.start(options)

            frames = []
            silence_frames = 0
            max_silence = int(SILENCE_DURATION * SAMPLE_RATE / FRAME_SIZE)

            while not done.is_set():
                data = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._stream.read(FRAME_SIZE, exception_on_overflow=False),
                )
                frames.append(data)
                amplitude = max(struct.unpack_from("h" * FRAME_SIZE, data))
                if amplitude < SILENCE_THRESHOLD:
                    silence_frames += 1
                else:
                    silence_frames = 0

                await conn.send(data)

                if silence_frames >= max_silence:
                    done.set()

            await conn.finish()

        except Exception as e:
            log.error("Deepgram transcription error: %s", e)

        return " ".join(transcript_parts)

    async def _transcribe_local(self) -> str:
        """Fallback: record audio until silence, then use whisper if available."""
        frames = []
        silence_frames = 0
        max_silence = int(SILENCE_DURATION * SAMPLE_RATE / FRAME_SIZE)

        while True:
            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._stream.read(FRAME_SIZE, exception_on_overflow=False),
            )
            frames.append(data)
            amplitude = max(struct.unpack_from("h" * FRAME_SIZE, data))
            if amplitude < SILENCE_THRESHOLD:
                silence_frames += 1
            else:
                silence_frames = 0
            if silence_frames >= max_silence and len(frames) > 10:
                break

        try:
            import whisper
            audio_bytes = b"".join(frames)
            buf = BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_bytes)
            buf.seek(0)
            model = whisper.load_model("base")
            result = model.transcribe(buf)
            return result.get("text", "")
        except ImportError:
            log.warning("whisper not installed — local transcription unavailable")
            return ""

    async def _stdin_stream(self) -> AsyncIterator[str]:
        loop = asyncio.get_event_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, input, "> ")
                if line.strip():
                    yield line.strip()
            except (EOFError, KeyboardInterrupt):
                break

    def _cleanup(self) -> None:
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception:
                pass

    def stop(self) -> None:
        self._active = False
