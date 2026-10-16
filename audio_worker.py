"""Microphone helper process for MTalk: records one take, then exits.

MTalk starts this ahead of time and drives it over stdin:
    "start\n"  open the microphone and begin recording
    "stop\n"   send the audio back and exit
The audio goes to stdout as an 8-byte little-endian length followed by raw
float32 samples (16 kHz mono).

Why a separate process: on macOS, stopping a PortAudio stream occasionally
deadlocks inside CoreAudio, which leaves the mic on and blocks every later
recording in that process. Here the audio is sent *before* the stream is
stopped, and if the stop hangs MTalk kills this process — which always
releases the microphone.
"""

import os
import struct
import sys
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000


def main():
    device = os.environ.get("MTALK_DEVICE") or None
    frames = []
    lock = threading.Lock()
    stream = None

    def callback(indata, n, time_info, status):
        with lock:
            frames.append(indata.copy())

    while True:
        cmd = sys.stdin.readline()
        if not cmd:  # MTalk went away
            return
        cmd = cmd.strip()
        if cmd == "start" and stream is None:
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    device=device,
                    callback=callback,
                )
                stream.start()
            except Exception as exc:
                print(f"[error] could not open microphone: {exc}", file=sys.stderr)
        elif cmd == "stop":
            with lock:
                data = b""
                if frames:
                    data = np.concatenate(frames).reshape(-1).astype(np.float32).tobytes()
            out = sys.stdout.buffer
            out.write(struct.pack("<Q", len(data)))
            out.write(data)
            out.flush()
            if stream is not None:
                stream.stop()  # may hang in CoreAudio; MTalk kills us if so
                stream.close()
            return


if __name__ == "__main__":
    main()
