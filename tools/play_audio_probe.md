The helper generates quiet PCM WAV data and sends it to one explicitly selected Windows output endpoint using the already installed PySide6 `QAudioSink`. It never opens a microphone, changes the Windows default endpoint, or calls a mute or volume setter. Only `play` starts an audio stream. Run as the ordinary desktop user, never as administrator.

From the repository directory:

```powershell
.\.venv\Scripts\python.exe tools/play_audio_probe.py list --output artifacts/audio-outputs-new.json
.\.venv\Scripts\python.exe tools/play_audio_probe.py generate --output artifacts/quiet-probe-new.wav
.\.venv\Scripts\python.exe tools/play_audio_probe.py play --wav artifacts/quiet-probe-new.wav --device-id '{exact endpoint ID from list}' --output artifacts/audio-play-new
```

All output paths must be new. The default WAV lasts 6 seconds, has 100 ms edge fades, and combines 440/660 Hz tones with digital amplitude at most 0.045. This is a low digital signal; actual acoustic loudness still depends on endpoint volume and speakers. The playback command accepts only 2–10 second mono PCM16 WAV files with peak at most 0.08. It converts the data to the selected endpoint's preferred Float32 format and records the resampling and PCM hash. Unsupported/missing endpoints fail explicitly without default fallback.

`report.json` records the exact selected endpoint ID, inventory, WAV/tool hashes before and after, stream start/end, actual state transitions, processed microseconds, and errors. `playback.jsonl` contains timestamped samples. A successful result means the output stream became active and processed the WAV to EOF. It **does not** prove audible sound or a visible PetWindow response. Pair these records with the independent, current PetWindow/provider evidence, using the same endpoint ID and interval. The helper cannot fill those checks.

On 2026-09-09, the read-only inventory showed:

| Output | Exact endpoint ID | Preferred format |
| --- | --- | --- |
| Speakers (USB Audio and HID) | `{0.0.0.00000000}.{7c82a0d4-53b3-4a39-b6f6-613ba8a41f14}` | 48000 Hz, stereo Float32 |
| Speakers (ToDesk Virtual Audio) | `{0.0.0.00000000}.{04f22472-85cd-46db-aad7-cb6611615f51}` | 44100 Hz, stereo Float32 |

Re-enumerate if a device is disconnected or reinstalled. Root's independent endpoint observation found USB muted and ToDesk unmuted at preparation time. A muted endpoint or low external volume can prevent the output meter and desktop pet from detecting the WAV even if the stream completes. This tool will not override that setting. Playback must remain pending until the authorized final acceptance step.

API references: [QAudioSink](https://doc.qt.io/qt-6.8/qaudiosink.html), [QAudioDevice](https://doc.qt.io/qt-6.8/qaudiodevice.html), [QMediaDevices](https://doc.qt.io/qt-6.8/qmediadevices.html). The installed PySide6 6.8.3 binding exposes the audio state/error enums as `QAudio`.
