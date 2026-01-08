#!/usr/bin/env python3
"""
AsyncIO сервер для приема изображений от нескольких клиентов.

Протокол (v2, с возобновлением передачи):
  - client_name: uint32(len) + bytes(utf-8)
  - file_size:   uint64 (байты)
  - filename:    uint32(len) + bytes(utf-8)
  - upload_id:   uint32(len) + bytes(utf-8)  (стабильный id для resume)
  - server_offset_response: uint64 (сколько байт уже есть на сервере)
  - image_body:  (file_size - offset) байт
  - final_response: b"OK" или b"ER"
"""

import asyncio
from dataclasses import dataclass
import os
import socket
import struct
from datetime import datetime
from typing import Dict, Optional

from Logger import Logger

@dataclass(frozen=True)
class ServerConfig:
    ip: str = "130.49.146.15"
    port: int = 8888
    images_dir: str = "/root/lorett/GroundLinkMonitorServer/received_images"
    log_level: str = "info"

    # Производительность
    chunk_size: int = 4 * 1024 * 1024  # 4 MB
    socket_buf: int = 8 * 1024 * 1024  # 8 MB
    stream_limit: int = 8 * 1024 * 1024  # >= chunk_size
    file_buffering: int = 4 * 1024 * 1024

    # Таймаут "тишины" при приёме: освобождает lock при зависшем канале
    file_idle_timeout: float = 60.0


class ProtocolV2:
    """Сериализация/десериализация полей протокола v2."""

    @staticmethod
    async def read_u32(reader: asyncio.StreamReader) -> int:
        data = await reader.readexactly(4)
        return struct.unpack("!I", data)[0]

    @staticmethod
    async def read_u64(reader: asyncio.StreamReader) -> int:
        data = await reader.readexactly(8)
        return struct.unpack("!Q", data)[0]

    @staticmethod
    async def read_string(reader: asyncio.StreamReader) -> str:
        n = await ProtocolV2.read_u32(reader)
        data = await reader.readexactly(n)
        return data.decode("utf-8")

    @staticmethod
    def write_u64(writer: asyncio.StreamWriter, value: int) -> None:
        writer.write(struct.pack("!Q", int(value)))


@dataclass(frozen=True)
class UploadSession:
    client_name: str
    filename: str
    upload_id: str
    file_size: int
    client_dir: str
    part_path: str
    done_path: str

    @staticmethod
    def safe_filename(name: str) -> str:
        base = os.path.basename(name)
        return base.replace("/", "_").replace("\\", "_")

    @classmethod
    def from_header(cls, images_dir: str, client_name: str, filename: str, upload_id: str, file_size: int) -> "UploadSession":
        client_dir = os.path.join(images_dir, client_name)
        safe = cls.safe_filename(filename)
        part_path = os.path.join(client_dir, f"{upload_id}_{safe}.part")
        done_path = os.path.join(client_dir, f"{upload_id}.done")
        return cls(
            client_name=client_name,
            filename=safe,
            upload_id=upload_id,
            file_size=file_size,
            client_dir=client_dir,
            part_path=part_path,
            done_path=done_path,
        )


class UploadManager:
    """Управляет локами по upload_id и финализацией на диске."""

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}

    def lock_for(self, upload_id: str) -> asyncio.Lock:
        lock = self._locks.get(upload_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[upload_id] = lock
        return lock

    @staticmethod
    def existing_offset(session: UploadSession) -> int:
        # Если есть done-маркер — считаем завершённым
        if os.path.exists(session.done_path):
            return session.file_size
        try:
            return os.path.getsize(session.part_path)
        except FileNotFoundError:
            return 0

    @staticmethod
    def reset_part(session: UploadSession) -> None:
        # Сбрасываем part, если он странный
        try:
            os.makedirs(session.client_dir, exist_ok=True)
            with open(session.part_path, "wb"):
                pass
        except Exception:
            pass

    @staticmethod
    def finalize(session: UploadSession) -> str:
        """
        Переименовывает .part в финальный файл и пишет .done.
        Возвращает абсолютный путь финального файла.
        """
        os.makedirs(session.client_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"{timestamp}_{session.filename}"
        final_path = os.path.join(session.client_dir, final_name)
        os.replace(session.part_path, final_path)
        with open(session.done_path, "w", encoding="utf-8") as m:
            m.write(final_name)
        return final_path

    @staticmethod
    def read_final_path(session: UploadSession) -> Optional[str]:
        try:
            with open(session.done_path, "r", encoding="utf-8") as m:
                final_name = (m.read() or "").strip()
            if not final_name:
                return None
            return os.path.join(session.client_dir, final_name)
        except Exception:
            return None


class SocketTuner:
    def __init__(self, socket_buf: int) -> None:
        self.socket_buf = socket_buf

    def tune(self, sock: socket.socket) -> None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_buf)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.socket_buf)


class FileReceiver:
    def __init__(self, chunk_size: int, idle_timeout: float) -> None:
        self.chunk_size = chunk_size
        self.idle_timeout = idle_timeout

    async def receive_exactly_to_file(self, reader: asyncio.StreamReader, file_obj, size: int) -> None:
        remaining = size
        while remaining > 0:
            n = min(self.chunk_size, remaining)
            try:
                chunk = await asyncio.wait_for(reader.readexactly(n), timeout=self.idle_timeout)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    file_obj.write(e.partial)
                raise ConnectionError("Соединение разорвано: клиент отключился")
            except asyncio.TimeoutError as e:
                raise ConnectionError(f"Таймаут при приёме файла (нет данных > {self.idle_timeout}s)") from e
            file_obj.write(chunk)
            remaining -= n


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
        self._uploads = UploadManager()
        self._socket_tuner = SocketTuner(socket_buf=self.config.socket_buf)
        self._receiver = FileReceiver(chunk_size=self.config.chunk_size, idle_timeout=self.config.file_idle_timeout)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        sock = writer.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            try:
                self._socket_tuner.tune(sock)
            except Exception:
                # Опции могут быть недоступны на некоторых платформах/обертках
                pass

        try:
            self.logger.info(f"Подключен клиент: {peer}")

            client_name = await ProtocolV2.read_string(reader)
            self.logger.info(f"Имя клиента: {client_name}")

            file_size = await ProtocolV2.read_u64(reader)
            filename = await ProtocolV2.read_string(reader)
            upload_id = await ProtocolV2.read_string(reader)

            session = UploadSession.from_header(
                images_dir=self.config.images_dir,
                client_name=client_name,
                filename=filename,
                upload_id=upload_id,
                file_size=file_size,
            )

            self.logger.info(
                f"Клиент {client_name} ({peer}) отправляет файл: {session.filename}, "
                f"size={session.file_size}, upload_id={session.upload_id}"
            )

            lock = self._uploads.lock_for(session.upload_id)
            async with lock:
                os.makedirs(session.client_dir, exist_ok=True)

                # Определяем сколько уже получено
                existing = self._uploads.existing_offset(session)

                # Если на диске больше, чем ожидается — сбрасываем (файл поменялся или upload_id ошибочный)
                if existing > session.file_size:
                    self._uploads.reset_part(session)
                    existing = 0

                # Сообщаем клиенту оффсет, с которого продолжать
                self.logger.info(f"Resume: upload_id={session.upload_id} offset={existing}/{session.file_size}")
                ProtocolV2.write_u64(writer, existing)
                await writer.drain()

                remaining = session.file_size - existing
                if remaining > 0:
                    # Дописываем с конца
                    with open(
                        session.part_path,
                        "r+b" if os.path.exists(session.part_path) else "wb",
                        buffering=self.config.file_buffering,
                    ) as f:
                        f.seek(existing)
                        await self._receiver.receive_exactly_to_file(reader, f, remaining)

                # Завершено: если ещё не помечено как done — финализируем.
                # Важно: используем done-маркер, чтобы повторное подключение после обрыва
                # (когда клиент не получил OK) не приводило к повторной загрузке.
                if not os.path.exists(session.done_path):
                    final_path = self._uploads.finalize(session)
                else:
                    final_path = self._uploads.read_final_path(session) or "unknown"

            self.logger.info(f"Файл сохранён: {final_path} ({session.file_size} байт)")

            writer.write(b"OK")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError, ConnectionError) as e:
            self.logger.error(f"Ошибка соединения с клиентом {peer}: {e}")
            try:
                writer.write(b"ER")
                await writer.drain()
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"Ошибка при работе с клиентом {peer}: {e}")
            try:
                writer.write(b"ER")
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def start(self) -> None:
        server = await asyncio.start_server(
            self.handle_client,
            host=self.config.ip,
            port=self.config.port,
            backlog=socket.SOMAXCONN,
            limit=max(self.config.stream_limit, self.config.chunk_size * 2),
        )

        # Настраиваем listening socket (recvbuf)
        for s in server.sockets or []:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.config.socket_buf)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.config.socket_buf)
            except Exception:
                pass

        addrs = ", ".join(str(s.getsockname()) for s in (server.sockets or []))
        self.logger.info(f"Сервер запущен на {addrs}")
        self.logger.info("Ожидание подключений...")

        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(ImageServer().start())
