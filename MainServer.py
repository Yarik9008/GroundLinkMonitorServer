#!/usr/bin/env python3
"""
SFTP (SSH) сервер для приёма файлов от клиентов.

Зачем:
- вместо собственного TCP-протокола используем стандартный SFTP
- клиент может докачивать (resume) файл через append в `.part`, затем rename в финальный файл

Куда сохраняем:
- корень SFTP = `images_dir`
- ожидаемый путь загрузки со стороны клиента:
    <client_name>/<upload_id>_<filename>.part
  после завершения клиент делает:
    rename -> <client_name>/<timestamp>_<filename>
    и пишет <client_name>/<upload_id>.done
"""

import asyncio
from dataclasses import dataclass
import os
import socket
import threading
import time
from typing import Optional

from Logger import Logger


try:
    import paramiko
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "Не найдено 'paramiko'. Установите зависимости:\n"
        "  pip install -r GroundLinkMonitorServer/requirements.txt\n"
    ) from e


@dataclass(frozen=True)
class ServerConfig:
    ip: str = "130.49.146.15"
    port: int = 8888  # SSH/SFTP port
    images_dir: str = "/root/lorett/GroundLinkMonitorServer/received_images"
    log_level: str = "info"

    socket_buf: int = 8 * 1024 * 1024  # 8 MB
    # Аутентификация
    username: str = "lorett"
    password: str = "lorett"

    # Host key (если файла нет — будет сгенерирован)
    host_key_path: str = "/root/lorett/GroundLinkMonitorServer/ssh_host_rsa_key"
    host_key_bits: int = 2048


class SocketTuner:
    def __init__(self, socket_buf: int) -> None:
        self.socket_buf = socket_buf

    def tune(self, sock: socket.socket) -> None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_buf)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.socket_buf)


def _sftp_errno(exc: Exception) -> int:
    if isinstance(exc, FileNotFoundError):
        return paramiko.SFTP_NO_SUCH_FILE
    if isinstance(exc, PermissionError):
        return paramiko.SFTP_PERMISSION_DENIED
    return paramiko.SFTP_FAILURE


class SimpleSFTPServer(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, root: str, logger: Logger, **kwargs):
        super().__init__(server, *args, **kwargs)
        self._root = os.path.abspath(root)
        self._logger = logger

    def _to_local(self, path: str) -> str:
        # SFTP paths are typically POSIX-like. Map them under root safely.
        p = (path or "").lstrip("/")
        local = os.path.abspath(os.path.join(self._root, p))
        if local != self._root and not local.startswith(self._root + os.sep):
            raise PermissionError("path escapes root")
        return local

    def list_folder(self, path):
        try:
            local = self._to_local(path)
            out = []
            for name in os.listdir(local):
                st = os.lstat(os.path.join(local, name))
                attr = paramiko.SFTPAttributes.from_stat(st)
                attr.filename = name
                out.append(attr)
            return out
        except Exception as e:
            return _sftp_errno(e)

    def stat(self, path):
        try:
            local = self._to_local(path)
            return paramiko.SFTPAttributes.from_stat(os.stat(local))
        except Exception as e:
            return _sftp_errno(e)

    def lstat(self, path):
        try:
            local = self._to_local(path)
            return paramiko.SFTPAttributes.from_stat(os.lstat(local))
        except Exception as e:
            return _sftp_errno(e)

    def mkdir(self, path, attr):
        try:
            local = self._to_local(path)
            os.makedirs(local, exist_ok=True)
            return paramiko.SFTP_OK
        except Exception as e:
            return _sftp_errno(e)

    def rmdir(self, path):
        try:
            local = self._to_local(path)
            os.rmdir(local)
            return paramiko.SFTP_OK
        except Exception as e:
            return _sftp_errno(e)

    def remove(self, path):
        try:
            local = self._to_local(path)
            os.remove(local)
            return paramiko.SFTP_OK
        except Exception as e:
            return _sftp_errno(e)

    def rename(self, oldpath, newpath):
        try:
            old_local = self._to_local(oldpath)
            new_local = self._to_local(newpath)
            os.makedirs(os.path.dirname(new_local), exist_ok=True)
            os.replace(old_local, new_local)
            return paramiko.SFTP_OK
        except Exception as e:
            return _sftp_errno(e)

    def open(self, path, flags, attr):
        try:
            local = self._to_local(path)
            os.makedirs(os.path.dirname(local), exist_ok=True)

            # os.open flags are passed from SFTP client. Use them directly.
            fd = os.open(local, flags, 0o644)

            # Choose a compatible mode for fdopen.
            # Always open in binary mode; allow both read and write if requested.
            if flags & os.O_WRONLY:
                mode = "ab" if (flags & os.O_APPEND) else "wb" if (flags & os.O_TRUNC) else "r+b"
            elif flags & os.O_RDWR:
                mode = "a+b" if (flags & os.O_APPEND) else "w+b" if (flags & os.O_TRUNC) else "r+b"
            else:
                mode = "rb"

            f = os.fdopen(fd, mode)
            handle = paramiko.SFTPHandle(flags)
            handle.filename = local
            handle.readfile = f
            handle.writefile = f
            return handle
        except Exception as e:
            return _sftp_errno(e)


class SSHAuthServer(paramiko.ServerInterface):
    def __init__(self, username: str, password: str, logger: Logger):
        super().__init__()
        self._username = username
        self._password = password
        self._logger = logger
        self._sftp_requested = threading.Event()

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username: str, password: str):
        if username == self._username and password == self._password:
            return paramiko.AUTH_SUCCESSFUL
        self._logger.warning(f"SSH auth failed for username={username!r}")
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_subsystem_request(self, channel, name: str):
        if name == "sftp":
            self._sftp_requested.set()
            return True
        return False


class ImageServer:
    def __init__(self, config: ServerConfig = ServerConfig()):
        self.config = config
        # Создаем директорию для логов
        logs_dir = "/root/lorett/GroundLinkMonitorServer/logs"
        os.makedirs(logs_dir, exist_ok=True)

        logger_config = {
            "log_level": self.config.log_level,
            "path_log": "/root/lorett/GroundLinkMonitorServer/logs/image_server_",
        }
        self.logger = Logger(logger_config)

        os.makedirs(self.config.images_dir, exist_ok=True)
        self._socket_tuner = SocketTuner(socket_buf=self.config.socket_buf)
        self._host_key = self._load_or_create_host_key()

    def _load_or_create_host_key(self) -> "paramiko.PKey":
        path = self.config.host_key_path
        try:
            if os.path.exists(path):
                return paramiko.RSAKey.from_private_key_file(path)

            os.makedirs(os.path.dirname(path), exist_ok=True)
            key = paramiko.RSAKey.generate(bits=self.config.host_key_bits)
            key.write_private_key_file(path)
            os.chmod(path, 0o600)
            self.logger.info(f"Создан новый host key: {path}")
            return key
        except Exception as e:
            raise RuntimeError(f"Не удалось загрузить/создать host key ({path}): {e}") from e

    def _handle_connection(self, client_sock: socket.socket, addr) -> None:
        transport: Optional["paramiko.Transport"] = None
        try:
            self.logger.info(f"Обработка SSH-сессии: {addr}")
            try:
                self._socket_tuner.tune(client_sock)
            except Exception:
                pass
            try:
                client_sock.settimeout(15.0)
            except Exception:
                pass

            transport = paramiko.Transport(client_sock)
            # Ограничиваем ожидание баннера/аутентификации (иначе зависшие клиенты держат поток)
            transport.banner_timeout = 15.0
            transport.auth_timeout = 15.0
            self.logger.debug(f"SSH transport создан: {addr}")
            transport.add_server_key(self._host_key)

            server = SSHAuthServer(self.config.username, self.config.password, logger=self.logger)
            try:
                self.logger.debug(f"Запуск SSH negotiation: {addr}")
                transport.start_server(server=server)
            except paramiko.SSHException as e:
                self.logger.error(f"SSH negotiation failed from {addr}: {e}")
                return

            # В server-mode каналы обычно нужно явно accept-ить,
            # иначе клиент может зависнуть на открытии session/subsystem.
            self.logger.debug(f"Ожидание SSH channel: {addr}")
            chan = transport.accept(20)
            if chan is None:
                self.logger.warning(f"SSH channel not opened by {addr} (timeout)")
                return
            self.logger.debug(f"SSH channel открыт: {addr}, channel={chan.get_id()}")

            # Ждём запрос подсистемы SFTP и запускаем обработчик вручную.
            if not server._sftp_requested.wait(15.0):
                self.logger.warning(f"SFTP subsystem not requested by {addr} (timeout)")
                return

            self.logger.debug(f"Запуск SFTP подсистемы: {addr}")
            sftp = paramiko.SFTPServer(
                chan,
                "sftp",
                server,
                SimpleSFTPServer,
                root=self.config.images_dir,
                logger=self.logger,
            )
            # Блокирует поток до закрытия канала/сессии
            sftp.start_subsystem("sftp", transport, chan)
        except Exception as e:
            self.logger.error(f"Ошибка SFTP-сессии от {addr}: {e}")
        finally:
            try:
                if transport is not None:
                    transport.close()
            except Exception:
                pass
            try:
                client_sock.close()
            except Exception:
                pass

    def _serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.config.socket_buf)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.config.socket_buf)
        except Exception:
            pass

        sock.bind((self.config.ip, self.config.port))
        sock.listen(socket.SOMAXCONN)
        self.logger.info(f"SFTP сервер запущен на {(self.config.ip, self.config.port)}")
        self.logger.info(f"SFTP root: {self.config.images_dir}")
        self.logger.info(f"Auth: username={self.config.username!r} (password задан в конфиге)")

        while True:
            client, addr = sock.accept()
            self.logger.info(f"Подключение: {addr}")
            t = threading.Thread(target=self._handle_connection, args=(client, addr), daemon=True)
            t.start()

    async def start(self) -> None:
        await asyncio.to_thread(self._serve_forever)


if __name__ == "__main__":
    asyncio.run(ImageServer().start())
