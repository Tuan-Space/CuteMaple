"""Explicit microphone recording and local attachment playback."""
from __future__ import annotations
import uuid
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,Signal,QTimer
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QPushButton,QLabel,QComboBox,QSlider
from PySide6.QtMultimedia import (QMediaPlayer,QAudioOutput,QMediaDevices,QMediaCaptureSession,
    QAudioInput,QMediaRecorder,QMediaFormat)


class MediaBar(QWidget):
    recorded=Signal(str)
    recordingStarted=Signal()
    def __init__(self,root,parent=None):
        super().__init__(parent); self.root=Path(root); self.path=None; self.failure=False; self.pending_finalize=False
        self.player=QMediaPlayer(self); self.output=QAudioOutput(self); self.player.setAudioOutput(self.output)
        self.capture=QMediaCaptureSession(self); self.audio_input=QAudioInput(self)
        self.recorder=QMediaRecorder(self); self.capture.setAudioInput(self.audio_input); self.capture.setRecorder(self.recorder)
        self.devices=QMediaDevices(self); self.devices.audioInputsChanged.connect(self.refresh_devices)
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout(); self.play=QPushButton('播放音频'); self.play.clicked.connect(self.toggle_play)
        self.seek=QSlider(Qt.Horizontal); self.seek.sliderMoved.connect(self.player.setPosition)
        self.play_time=QLabel('00:00'); row.addWidget(self.play); row.addWidget(self.seek,1); row.addWidget(self.play_time); layout.addLayout(row)
        self.player.positionChanged.connect(self.position); self.player.durationChanged.connect(lambda n:self.seek.setRange(0,n))
        row=QHBoxLayout(); self.inputs=QComboBox(); self.start=QPushButton('开始录音'); self.pause=QPushButton('暂停'); self.stop=QPushButton('结束并保存')
        row.addWidget(self.inputs,1)
        for w in (self.start,self.pause,self.stop):row.addWidget(w)
        layout.addLayout(row); self.status=QLabel('麦克风未使用'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.start.clicked.connect(self.begin); self.pause.clicked.connect(self.pause_recording); self.stop.clicked.connect(self.finish)
        self.recorder.durationChanged.connect(lambda n:self.status.setText(('录音已暂停 · ' if self.recorder.recorderState()==QMediaRecorder.PausedState else '● 正在使用麦克风 · ')+f'{n//60000:02}:{n//1000%60:02}'))
        self.recorder.recorderStateChanged.connect(self.state_changed); self.recorder.errorOccurred.connect(self.record_error)
        self.player.errorOccurred.connect(lambda *_:self.status.setText('音频无法播放：'+self.player.errorString()+'；可打开附件文件夹。'))
        self.refresh_devices(); self.state_changed(QMediaRecorder.StoppedState)

    def refresh_devices(self):
        old=self.inputs.currentData(); self.inputs.clear(); self.inputs.addItem('系统默认麦克风',b'')
        for device in QMediaDevices.audioInputs():self.inputs.addItem(device.description(),bytes(device.id()))
        match=self.inputs.findData(old)
        if match>=0:self.inputs.setCurrentIndex(match)

    def position(self,n):
        if not self.seek.isSliderDown():self.seek.setValue(n)
        self.play_time.setText(f'{n//60000:02}:{n//1000%60:02}')

    def load(self,path):
        self.player.setSource(QUrl.fromLocalFile(str(path))); self.player.play(); self.play.setText('暂停音频')

    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlayingState:
            self.player.pause(); self.play.setText('播放音频')
        else:self.player.play(); self.play.setText('暂停音频')

    def begin(self):
        if self.recorder.recorderState()!=QMediaRecorder.StoppedState or self.pending_finalize:return
        device=QMediaDevices.defaultAudioInput()
        if self.inputs.currentData():
            device=next((d for d in QMediaDevices.audioInputs() if bytes(d.id())==self.inputs.currentData()),device)
        if device.isNull():self.status.setText('没有可用麦克风，请连接设备并检查 Windows 麦克风权限。');return
        self.audio_input.setDevice(device)
        fmt=QMediaFormat(QMediaFormat.Wave); fmt.setAudioCodec(QMediaFormat.AudioCodec.Wave)
        if not fmt.isSupported(QMediaFormat.Encode):self.status.setText('当前录音后端不支持 WAV，请检查安装完整性。');return
        if hasattr(self,'prepare_recording'):
            try:self.prepare_recording()
            except Exception as error:self.status.setText('不能开始录音：'+str(error));return
        self.recorder.setMediaFormat(fmt)
        preferred=device.preferredFormat()
        self.recorder.setAudioSampleRate(preferred.sampleRate())
        self.recorder.setAudioChannelCount(preferred.channelCount())
        self.path=self.root/'recordings'/(datetime.now().strftime('录音-%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'.wav')
        self.failure=False; self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self.path))); self.recordingStarted.emit(); self.recorder.record()

    def pause_recording(self):
        if self.recorder.recorderState()==QMediaRecorder.PausedState:self.recorder.record()
        elif self.recorder.recorderState()==QMediaRecorder.RecordingState:self.recorder.pause()

    def finish(self):
        if self.recorder.recorderState()!=QMediaRecorder.StoppedState:self.recorder.stop()
        self.player.stop()

    def record_error(self,*_):
        self.failure=True
        self.status.setText('录音异常：'+self.recorder.errorString()+'。已写入的文件保留在资料库 recordings 中。')

    def state_changed(self,state):
        stopped=state==QMediaRecorder.StoppedState
        self.start.setEnabled(stopped); self.inputs.setEnabled(stopped); self.pause.setEnabled(not stopped); self.stop.setEnabled(not stopped)
        self.pause.setText('继续' if state==QMediaRecorder.PausedState else '暂停')
        if stopped and self.path:
            path=self.path; self.path=None
            if not self.failure and path.exists():
                self.pending_finalize=True;self.start.setEnabled(False)
                QTimer.singleShot(100,lambda:self.finalize_recording(path))
            elif not self.failure:self.status.setText('没有录到有效音频；请检查麦克风。')

    def finalize_recording(self,path):
        import wave
        try:
            with wave.open(str(path),'rb') as recording:
                if recording.getnframes()==0:
                    self.status.setText('没有收到麦克风音频，未保存为有效录音。请检查输入设备或 Windows 麦克风权限。');return
            self.status.setText('录音已停止，正在保存到笔记。');self.recorded.emit(str(path))
        except Exception as error:
            self.status.setText('录音尚未完整写入，原文件已保留：'+str(error))
        finally:
            self.pending_finalize=False;self.start.setEnabled(True)
