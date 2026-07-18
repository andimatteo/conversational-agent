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
        while self._running:
            try:
                self._buf += self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            while len(self._buf) >= PUMP_CHUNK and self._running:
                chunk, self._buf = self._buf[:PUMP_CHUNK], self._buf[PUMP_CHUNK:]
                if self.peer and self.peer.input_callback:
                    self.peer.input_callback(chunk)
                if self._speaker:
                    try:
                        self._speaker.write(chunk)
                    except Exception:
                        self._speaker = None
                time.sleep(len(chunk) / BYTES_PER_SEC)  # pace to real time


def wire(a: BridgeAudioInterface, b: BridgeAudioInterface):
    a.peer, b.peer = b, a
