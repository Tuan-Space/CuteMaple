"""User-scoped, fixed-command graceful shutdown for the installer."""
import hashlib
import os
from PySide6.QtCore import QCoreApplication,QTimer
from PySide6.QtNetwork import QLocalServer,QLocalSocket


def server_name():
    user=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','default')
    return 'CuteMaple-220-'+hashlib.sha256(user.encode()).hexdigest()[:20]


def request_quit():
    app=QCoreApplication.instance() or QCoreApplication([])
    socket=QLocalSocket();socket.connectToServer(server_name())
    if not socket.waitForConnected(1500):return 0
    socket.write(b'quit-for-upgrade');socket.flush()
    if not socket.waitForReadyRead(12000):return 2
    return 0 if bytes(socket.readAll())==b'ok' else 3


def install_server(pet):
    server=QLocalServer(pet);server.setSocketOptions(QLocalServer.UserAccessOption)
    QLocalServer.removeServer(server_name())
    if not server.listen(server_name()):return None
    def accepted():
        socket=server.nextPendingConnection()
        def received():
            command=bytes(socket.readAll())
            if command==b'quit-for-upgrade':
                pet.quit_app()
                def respond(attempt=0):
                    if not pet._quitting and pet.journal and pet.journal.window.note_editor.media.pending_finalize and attempt<80:
                        QTimer.singleShot(100,lambda:respond(attempt+1));return
                    socket.write(b'ok' if pet._quitting else b'unsaved');socket.flush();socket.waitForBytesWritten(500);socket.disconnectFromServer()
                respond()
            else:
                socket.write(b'rejected');socket.flush();socket.waitForBytesWritten(500);socket.disconnectFromServer()
        socket.readyRead.connect(received);socket.disconnected.connect(socket.deleteLater)
        if socket.bytesAvailable():received()
    server.newConnection.connect(accepted);return server
