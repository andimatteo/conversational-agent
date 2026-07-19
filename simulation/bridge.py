"""Agent-to-agent audio bridge.

Two live ElevenLabs conversations run at once (negotiator + counterparty).
Each side's spoken audio (pcm_16000) is pumped into the other side's mic
input AT REAL-TIME RATE, so turn-taking, silence and barge-in behave like a
phone line — this is a real negotiation, not two prompts passing text.

Both sides' audio is written to a WAV per side for local recording; the
authoritative recording + transcript live in the ElevenLabs conversation.
"""
import queue
import threading
import time
import wave
from pathlib import Path

from elevenlabs.conversational_ai.conversation import AudioInterface

SAMPLE_RATE = 16000
BYTES_PER_SEC = SAMPLE_RATE * 2  # 16-bit mono
PUMP_CHUNK = 4000                # 125 ms of audio per pump tick


class BridgeAudioInterface(AudioInterface):
    """One side of the line. `output()` receives this side's agent speech and
    pumps it to the peer's input callback, paced to real time."""

    def __init__(self, label: str, wav_path: Path, listen: bool = False):
        self.label = label
        self.peer: "BridgeAudioInterface | None" = None
        self.input_callback = None
        self._q: queue.Queue[bytes] = queue.Queue()
        self._running = False
        self._buf = b""
        self._wav = wave.open(str(wav_path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)
        self._speaker = None
        if listen:  # optional local playback so the room hears the call
            try:
                import pyaudio
                pa = pyaudio.PyAudio()
                self._speaker = pa.open(format=pyaudio.paInt16, channels=1,
                                        rate=SAMPLE_RATE, output=True)
            except Exception as e:
                print(f"[{self.label}] no local playback ({e}) — continuing silent")

    # -- AudioInterface contract -------------------------------------------
    def start(self, input_callback):
        self.input_callback = input_callback
        self._running = True
        threading.Thread(target=self._pump, daemon=True, name=f"pump-{self.label}").start()

    def stop(self):
        self._running = False
        try:
            self._wav.close()
        except Exception:
            pass

    def output(self, audio: bytes):
        self._wav.writeframes(audio)
        self._q.put(audio)

    def interrupt(self):
        # Our agent got barged-in on: flush unspoken audio so the peer
        # stops "hearing" speech that was never finished.
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._buf = b""

    # -- real-time pump -----------------------------------------------------
    def _pump(self):
        """Emulates a phone line, which is never byte-silent: once the peer is
        on the line it receives a CONTINUOUS real-time stream — real speech
        when this agent talks, zero-frames otherwise (the peer's ASR needs the
        silence to close turns). Speech produced before the peer connects is
        HELD, not dropped — losing the counterparty's greeting deadlocked the
        first live call (both sides waited forever)."""
        silence = b"\x00" * PUMP_CHUNK
        while self._running:
            try:  # drain everything queued into the buffer, without blocking
                while True:
                    self._buf += self._q.get_nowait()
            except queue.Empty:
                pass
            if not (self.peer and self.peer.input_callback):
                time.sleep(0.05)  # hold any buffered speech until they pick up
                continue
            if self._buf:
                chunk, self._buf = self._buf[:PUMP_CHUNK], self._buf[PUMP_CHUNK:]
                if len(chunk) < PUMP_CHUNK:  # flush utterance tails padded out
                    chunk += b"\x00" * (PUMP_CHUNK - len(chunk))
                speech = True
            else:
                chunk, speech = silence, False
            try:
                self.peer.input_callback(chunk)
            except Exception:
                pass
            if self._speaker and speech:
                try:
                    self._speaker.write(chunk)
                except Exception:
                    self._speaker = None
            time.sleep(PUMP_CHUNK / BYTES_PER_SEC)  # pace to real time


def wire(a: BridgeAudioInterface, b: BridgeAudioInterface):
    a.peer, b.peer = b, a
