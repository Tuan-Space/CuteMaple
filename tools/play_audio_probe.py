"""Create and explicitly play a quiet WAV on one Windows output endpoint.

This is real output-stream evidence, not proof of audibility or a PetWindow reaction.
Only the ``play`` subcommand opens a playback stream. No microphone, default-device,
endpoint-mute, endpoint-volume, or application-reaction APIs are called.
"""
from __future__ import annotations

import argparse
from array import array
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import wave


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def little_endian_bytes(values: array) -> bytes:
    if sys.byteorder != 'little':
        values.byteswap()
    return values.tobytes()


def read_quiet_wav(path: Path) -> tuple[list[float], int, dict]:
    with wave.open(str(path), 'rb') as source:
        rate, count = source.getframerate(), source.getnframes()
        if source.getcomptype() != 'NONE' or source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError('Only uncompressed mono PCM16 WAV is accepted.')
        if not 8000 <= rate <= 96000 or not 2 <= count / rate <= 10:
            raise ValueError('WAV must last 2..10 seconds at 8000..96000 Hz.')
        raw = source.readframes(count)
    if len(raw) != count * 2:
        raise ValueError('WAV frame data is truncated.')
    pcm = array('h')
    pcm.frombytes(raw)
    if sys.byteorder != 'little':
        pcm.byteswap()
    values = [sample / 32768 for sample in pcm]
    peak = max(abs(value) for value in values)
    if not 0 < peak <= 0.08:
        raise ValueError('Digital peak must be greater than zero and no more than 0.08 (-21.94 dBFS).')
    return values, rate, {'path': str(path), 'sha256': sha256(path), 'sampleRate': rate,
        'frames': count, 'channels': 1, 'durationSeconds': count / rate, 'digitalPeak': peak,
        'peakDbFS': 20 * math.log10(peak)}


def render_float_pcm(values: list[float], source_rate: int, rate: int, channels: int) -> tuple[bytes, int]:
    """Deterministic linear resampling and channel duplication; no gain or recording."""
    if not 8000 <= rate <= 192000 or not 1 <= channels <= 8:
        raise ValueError('Selected endpoint has an unsupported preferred format.')
    frames = round(len(values) * rate / source_rate)
    output = array('f')
    for frame in range(frames):
        position = frame * source_rate / rate
        left = min(int(position), len(values) - 1)
        right = min(left + 1, len(values) - 1)
        fraction = position - left
        value = values[left] * (1 - fraction) + values[right] * fraction
        output.extend([value] * channels)
    return little_endian_bytes(output), frames


def generate(options) -> int:
    path = options.output.resolve()
    sidecar = path.with_suffix(path.suffix + '.json')
    if path.exists() or sidecar.exists():
        raise ValueError('WAV or sidecar already exists; evidence is never overwritten.')
    if not 2 <= options.seconds <= 10 or not 0.01 <= options.amplitude <= 0.08:
        raise ValueError('Use 2..10 seconds and digital amplitude 0.01..0.08.')
    rate = 48000
    count = round(rate * options.seconds)
    pcm = array('h')
    for index in range(count):
        seconds = index / rate
        edge = min(1.0, index / (rate * 0.1), (count - 1 - index) / (rate * 0.1))
        fade = 0.5 - 0.5 * math.cos(math.pi * edge)
        envelope = 0.85 + 0.15 * math.sin(2 * math.pi * 2 * seconds)
        tone = 0.7 * math.sin(2 * math.pi * 440 * seconds) + 0.3 * math.sin(2 * math.pi * 660 * seconds)
        pcm.append(round(32767 * options.amplitude * fade * envelope * tone))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        with wave.open(stream, 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(little_endian_bytes(pcm))
    _, _, details = read_quiet_wav(path)
    details.update({'reportKind': 'cutemaple-audio-probe-wave', 'createdAtUnix': time.time(),
        'generatorPath': str(Path(__file__).resolve()), 'generatorSha256': sha256(Path(__file__)),
        'requestedAmplitude': options.amplitude, 'fadeSeconds': 0.1, 'tonesHz': [440, 660],
        'playbackStarted': False})
    write_json(sidecar, details)
    print(json.dumps(details, ensure_ascii=True))
    return 0


def ordinary_windows() -> None:
    if os.name != 'nt' or ctypes.windll.shell32.IsUserAnAdmin():
        raise ValueError('Enumerate or play only as an ordinary Windows user, never as administrator.')


def device_info(device) -> dict:
    fmt = device.preferredFormat()
    return {'id': bytes(device.id()).decode('utf-8', errors='strict'),
        'idHex': bytes(device.id()).hex(), 'description': device.description(),
        'isDefault': device.isDefault(), 'preferredSampleRate': fmt.sampleRate(),
        'preferredChannels': fmt.channelCount(), 'preferredSampleFormat': fmt.sampleFormat().name,
        'supportedSampleFormats': [value.name for value in device.supportedSampleFormats()]}


def enumerate_devices(options) -> int:
    ordinary_windows()
    from PySide6.QtCore import QCoreApplication, qVersion
    from PySide6.QtMultimedia import QMediaDevices
    app = QCoreApplication([])
    report = {'reportKind': 'cutemaple-audio-output-inventory', 'createdAtUnix': time.time(),
        'qtVersion': qVersion(), 'outputs': [device_info(device) for device in QMediaDevices.audioOutputs()],
        'playbackStarted': False, 'microphoneOpened': False}
    if options.output:
        path = options.output.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, report)
    print(json.dumps(report, ensure_ascii=True))
    return 0


def play(options) -> int:
    ordinary_windows()
    from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice, QTimer, qVersion
    from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

    output = options.output.resolve()
    if output.exists():
        raise ValueError('Output directory already exists; playback evidence is never overwritten.')
    wav_path = options.wav.resolve(strict=True)
    values, source_rate, wav_details = read_quiet_wav(wav_path)
    source_path = Path(__file__).resolve()
    source_hash = sha256(source_path)
    app = QCoreApplication([])
    devices = QMediaDevices.audioOutputs()
    matches = [device for device in devices if device_info(device)['id'] == options.device_id]
    if len(matches) != 1:
        raise ValueError('The exact selected endpoint ID is absent or ambiguous; no default fallback is permitted.')
    selected = matches[0]
    fmt = selected.preferredFormat()
    if fmt.sampleFormat() != QAudioFormat.SampleFormat.Float or not selected.isFormatSupported(fmt):
        raise ValueError('This helper requires the selected endpoint to support its preferred Float PCM format.')
    raw, frames = render_float_pcm(values, source_rate, fmt.sampleRate(), fmt.channelCount())
    rendered_seconds = frames / fmt.sampleRate()
    output.mkdir(parents=True)
    stream = (output / 'playback.jsonl').open('x', encoding='utf-8')
    report = {'schemaVersion': 1, 'reportKind': 'cutemaple-real-audio-output',
        'scope': 'explicit-output-stream-only', 'pid': os.getpid(), 'qtVersion': qVersion(),
        'createdAtUnix': time.time(), 'selectedDevice': device_info(selected),
        'inventoryBefore': [device_info(device) for device in devices], 'sourceWav': wav_details,
        'tool': {'path': str(source_path), 'sha256AtStart': source_hash},
        'renderedPcm': {'sampleRate': fmt.sampleRate(), 'channels': fmt.channelCount(),
            'format': 'Float32LE', 'frames': frames, 'durationSeconds': rendered_seconds,
            'sha256': hashlib.sha256(raw).hexdigest(), 'gainApplied': 1.0,
            'resampling': 'linear' if source_rate != fmt.sampleRate() else 'none'},
        'ordinaryUser': True, 'microphoneOpened': False, 'reactionInjected': False,
        'defaultDeviceSetterCalled': False, 'endpointMuteSetterCalled': False,
        'endpointVolumeSetterCalled': False, 'streamVolumeSetterCalled': False,
        'audibilityVerified': False, 'petWindowReactionVerified': False,
        'muteOrGlobalVolumeMayPreventDetection': True, 'playbackCompleted': False,
        'errors': [], 'stateEvents': []}
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError('Could not open generated PCM buffer.')
    sink = QAudioSink(selected, fmt)
    monitor = QMediaDevices()
    started = time.monotonic()
    ending = False
    active = False
    last_sample = -1.0
    timer = QTimer()

    def record(kind: str, **fields) -> None:
        stream.write(json.dumps({'kind': kind, 'atUnix': time.time(),
            'elapsedSeconds': time.monotonic() - started, **fields}, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()

    def sample() -> dict:
        return {'state': sink.state().name, 'error': sink.error().name,
            'processedMicroseconds': sink.processedUSecs(), 'elapsedMicroseconds': sink.elapsedUSecs(),
            'bufferBytesRead': buffer.pos(), 'bufferBytesTotal': len(raw), 'bufferAtEnd': buffer.atEnd()}

    def finish(error: str | None = None) -> None:
        nonlocal ending
        if ending:
            return
        ending = True
        timer.stop()
        final = sample()
        report['streamEnd'] = final
        report['finishedAtUnix'] = time.time()
        if error:
            report['errors'].append(error)
        report['playbackCompleted'] = not report['errors']
        record('playback-ending', completed=report['playbackCompleted'], error=error, **final)
        sink.reset()
        buffer.close()
        QTimer.singleShot(0, app.quit)

    def state_changed(state) -> None:
        nonlocal active
        value = sample()
        report['stateEvents'].append({'atUnix': time.time(), **value})
        record('state', **value)
        if state == QAudio.State.ActiveState:
            active = True
            report.setdefault('activeAtUnix', time.time())

    def outputs_changed() -> None:
        current = [device_info(device) for device in QMediaDevices.audioOutputs()]
        record('output-inventory-changed', outputs=current)
        if options.device_id not in {device['id'] for device in current}:
            finish('Selected output endpoint disappeared; playback was stopped without fallback.')

    def poll() -> None:
        nonlocal last_sample
        elapsed = time.monotonic() - started
        value = sample()
        if elapsed - last_sample >= 0.25:
            record('sample', **value)
            last_sample = elapsed
        if sink.error() not in (QAudio.Error.NoError, QAudio.Error.UnderrunError):
            finish('Output stream failed: ' + sink.error().name)
        elif active and sink.state() == QAudio.State.IdleState and buffer.atEnd():
            if sink.processedUSecs() < (rendered_seconds - 0.1) * 1_000_000:
                finish('Output became idle before the complete WAV duration was processed.')
            else:
                finish()
        elif elapsed > rendered_seconds + 5:
            finish('Bounded output timeout; real stream completion was not observed.')

    def begin() -> None:
        report['playbackStartRequestedAtUnix'] = time.time()
        record('playback-start-requested', selectedDevice=report['selectedDevice'])
        sink.start(buffer)
        timer.start(50)

    sink.stateChanged.connect(state_changed)
    monitor.audioOutputsChanged.connect(outputs_changed)
    timer.timeout.connect(poll)
    QTimer.singleShot(0, begin)
    record('prepared', report=report)
    try:
        app.exec()
    finally:
        if not ending:
            finish('Event loop ended before output completion.')
        report['inventoryAfter'] = [device_info(device) for device in QMediaDevices.audioOutputs()]
        report['sourceWavSha256AtEnd'] = sha256(wav_path) if wav_path.exists() else None
        report['tool']['sha256AtEnd'] = sha256(source_path) if source_path.exists() else None
        report['inputsFrozen'] = (report['sourceWavSha256AtEnd'] == wav_details['sha256']
            and report['tool']['sha256AtEnd'] == source_hash)
        if not report['inputsFrozen']:
            report['errors'].append('WAV or helper source changed during playback.')
        report['playbackCompleted'] = report['playbackCompleted'] and not report['errors']
        report['activeStateObserved'] = active
        report['status'] = 'OUTPUT-STREAM-COMPLETED' if report['playbackCompleted'] else 'FAILED'
        stream.close()
        report['playbackLogSha256'] = sha256(output / 'playback.jsonl')
        write_json(output / 'report.json', report)
    print(json.dumps({'report': str(output / 'report.json'), 'status': report['status'],
        'selectedDevice': report['selectedDevice'], 'errors': report['errors']}, ensure_ascii=True))
    return 0 if report['playbackCompleted'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inventory = commands.add_parser('list', help='Read output endpoints; do not create a playback stream.')
    inventory.add_argument('--output', type=Path)
    inventory.set_defaults(action=enumerate_devices)
    create = commands.add_parser('generate', help='Create a low-amplitude test WAV without playing it.')
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--seconds', type=float, default=6)
    create.add_argument('--amplitude', type=float, default=0.045)
    create.set_defaults(action=generate)
    playback = commands.add_parser('play', help='Play real audio on the exact selected output endpoint.')
    playback.add_argument('--wav', type=Path, required=True)
    playback.add_argument('--device-id', required=True, help='Exact Windows endpoint ID from list; no automatic fallback.')
    playback.add_argument('--output', type=Path, required=True, help='New evidence directory, never overwritten.')
    playback.set_defaults(action=play)
    options = parser.parse_args()
    try:
        return options.action(options)
    except (ValueError, OSError, RuntimeError, wave.Error) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
