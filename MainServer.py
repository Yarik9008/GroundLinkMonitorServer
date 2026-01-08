#!/usr/bin/env python3
"""
AsyncIO сервер для приема изображений от нескольких клиентов.

Протокол (совместим с клиентом из этого репозитория):
  - client_name: uint32(len) + bytes(utf-8)
  - image_size:  uint32 (байты)
  - filename:    uint32(len) + bytes(utf-8)
  - image_body:  image_size байт
  - response:    b"OK" или b"ER"
"""

import asyncio
import os
import socket
import struct
from datetime import datetime

from Logger import Logger

# Размер чанка для передачи данных (1 MB) - должен совпадать с размером на клиенте
CHUNK_SIZE = 1024 * 1024  # 1 MB

# Буферы сокета (помогает на высоких скоростях / больших файлах)
SOCKET_BUF = 8 * 1024 * 1024  # 8 MB


def _set_socket_opts(sock: socket.socket) -> None:
    # TCP_NODELAY на всякий случай; при больших чанках эффект небольшой, но не мешает
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF)


async def _read_u32(reader: asyncio.StreamReader) -> int:
    data = await reader.readexactly(4)
    return struct.unpack("!I", data)[0]


async def _read_string(reader: asyncio.StreamReader) -> str:
    n = await _read_u32(reader)
    data = await reader.readexactly(n)
    return data.decode("utf-8")


async def _receive_to_file(reader: asyncio.StreamReader, file_obj, size: int) -> None:
    remaining = size
    while remaining > 0:
        chunk = await reader.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise ConnectionError("Соединение разорвано: клиент отключился")
        file_obj.write(chunk)
        remaining -= len(chunk)


class ImageServer:
    def __init__(
        self,
        ip: str = "130.49.146.15",
        port: int = 8888,
        images_dir: str = "/root/lorett/GroundLinkMonitorServer/received_images",
        log_level: str = "info",
    ):
        self.ip = ip
        self.port = port
        self.images_dir = images_dir

        # Создаем директорию для логов
        logs_dir = "/root/lorett/GroundLinkMonitorServer/logs"
        os.makedirs(logs_dir, exist_ok=True)

        logger_config = {
            "log_level": log_level,
            "path_log": "/root/lorett/GroundLinkMonitorServer/logs/image_server_",
        }
        self.logger = Logger(logger_config)

        os.makedirs(self.images_dir, exist_ok=True)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        sock = writer.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            try:
                _set_socket_opts(sock)
            except Exception:
                # Опции могут быть недоступны на некоторых платформах/обертках
                pass

        try:
            self.logger.info(f"Подключен клиент: {peer}")

            client_name = await _read_string(reader)
            self.logger.info(f"Имя клиента: {client_name}")

            client_dir = os.path.join(self.images_dir, client_name)
            os.makedirs(client_dir, exist_ok=True)

            image_size = await _read_u32(reader)
            filename = await _read_string(reader)

            self.logger.info(f"Клиент {client_name} ({peer}) отправляет изображение размером {image_size} байт")
            self.logger.debug(f"Имя файла: {filename}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_filename = f"{timestamp}_{filename}"
            save_path = os.path.join(client_dir, save_filename)

            with open(save_path, "wb", buffering=4 * 1024 * 1024) as f:
                await _receive_to_file(reader, f, image_size)

            self.logger.info(f"Изображение сохранено: {save_path} ({image_size} байт)")

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
            host=self.ip,
            port=self.port,
            backlog=socket.SOMAXCONN,
        )

        # Настраиваем listening socket (recvbuf)
        for s in server.sockets or []:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF)
            except Exception:
                pass

        addrs = ", ".join(str(s.getsockname()) for s in (server.sockets or []))
        self.logger.info(f"Сервер запущен на {addrs}")
        self.logger.info("Ожидание подключений...")

        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(ImageServer().start())
