import os
import re
import asyncio
import aiohttp
from datetime import date, datetime, timedelta, timezone
from pprint import pprint
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import urlopen
from Logger import Logger


class EusLogPortal:
    """Отвечает за взаимодействие с порталом EUS.

    Загружает страницы со списком станций, парсит ссылки на пролеты и
    сохраняет файлы логов локально.
    """
    # Инициализация
    def __init__(self, logger: Logger) -> None:
        """Инициализирует источники, параметры дат и регулярные выражения.

        Args:
            logger: Экземпляр Logger из Logger.py.
        """
        if logger is None:
            raise ValueError("logger is required")
        self.logger = logger

        self.data_passes = {}

        # Источники и параметры запроса.
        # http://eus.lorett.org/eus/logs_list.html - портал неоперативных станций
        # http://eus.lorett.org/eus/logs.html - портал оперативных станций
        self.urls = [
            "http://eus.lorett.org/eus/logs_list.html",
            "http://eus.lorett.org/eus/logs.html",
        ]

        # t0 - начальная дата, t1 - конечная дата (формат ГГГГ-ММ-ДД).
        today = datetime.now(timezone.utc).date()
        self.params = {
            "t0": today.isoformat(),
            "t1": (today + timedelta(days=1)).isoformat(),
        }

        # Регулярные выражения для станций, строк таблицы, ячеек и ссылок на пролеты.
        # Ссылка на станцию: забираем значение stid.
        self.station_re = re.compile(r"logstation\.html\?stid=([^&\"']+)", re.I)

        # Строка таблицы с датой в формате YYYY-MM-DD и хвостом строки.
        self.date_row_re = re.compile(
            r"<tr>\s*<td[^>]*>\s*<b>\s*(\d{4}-\d{2}-\d{2})\s*</b>\s*</td>(.*?)</tr>",
            re.I | re.S,
        )

        # Содержимое ячеек <td> (включая многострочные).
        self.td_re = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)

        # Пара ссылок: log_view и log_get в пределах одной ячейки.
        self.pass_re = re.compile(
            r"href=['\"](log_view/[^'\"]+)['\"].*?"
            r"href=['\"](log_get/[^'\"]+)['\"]",
            re.I | re.S,
        )

        self.logger.info("EusLogPortal initialized")

    # Валидация диапазона дат
    def _validate_date_range(self, start_value: date, end_value: date) -> None:
        """Проверяет, что конец диапазона строго на день позже начала."""
        self.logger.debug(f"validate dates: start={start_value}, end={end_value}")
        if end_value <= start_value:
            raise ValueError("end_day must be later than start_day")

    # Построение параметров дат
    def _build_date_params(self, start_day=None, end_day=None):
        """Строит параметры t0/t1 для даты или диапазона дат.

        Пример:
            Вход: start_day="2026-01-24", end_day="2026-01-25"
            Выход: {"t0": "2026-01-24", "t1": "2026-01-25"}
        """
        self.logger.debug(f"build date params: start_day={start_day}, end_day={end_day}")
        if start_day is None and end_day is None:
            start_value = datetime.now(timezone.utc).date()
            end_value = start_value + timedelta(days=1)
        else:
            if start_day is not None:
                start_value = start_day if isinstance(start_day, date) else date.fromisoformat(start_day)
            else:
                start_value = None
            if end_day is not None:
                end_value = end_day if isinstance(end_day, date) else date.fromisoformat(end_day)
            else:
                end_value = None

            if start_value is None and end_value is not None:
                start_value = end_value
            if end_value is None and start_value is not None:
                end_value = start_value + timedelta(days=1)

        self._validate_date_range(start_value, end_value)

        return {
            "t0": start_value.isoformat(),
            "t1": end_value.isoformat(),
        }

    # Скачивание одного файла лога (async)
    async def _download_single_log(
        self,
        session: aiohttp.ClientSession,
        sem: asyncio.Semaphore,
        url: str,
        out_dir: str,
    ) -> str:
        """Скачивает один файл лога, если он еще не сохранен, и возвращает путь."""
        # Скачиваем файл лога, если он еще не сохранен локально.
        os.makedirs(out_dir, exist_ok=True)

        filename = os.path.basename(urlparse(url).path)
        path = os.path.join(out_dir, filename)

        if os.path.exists(path) and os.path.getsize(path) > 0:
            self.logger.debug( f"file exists, skip: {path}")
            return path

        async with sem:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(8192):
                        f.write(chunk)

        self.logger.debug( f"file saved: {path}")
        return path
    
    # Скачивание списка логов (async)
    async def _download_logs_async(self, tasks: list, max_parallel: int = 10) -> list:
        """Скачивает список пролетов и возвращает результаты (async)."""
        sem = asyncio.Semaphore(max_parallel)
        async with aiohttp.ClientSession() as session:
            download_tasks = []
            for get_url, out_dir in tasks:
                download_tasks.append(self._download_single_log(session, sem, get_url, out_dir))
            return await asyncio.gather(*download_tasks, return_exceptions=True)

    # Получение текста страницы
    def load_html(self, url: str, params=None) -> str:
        """Получает HTML по URL с параметрами диапазона дат (если заданы).

        Пример:
            Вход: url="http://eus.lorett.org/eus/logs.html"
            Выход: "<html>...</html>"
        """
        # Получаем HTML с параметрами диапазона дат (если заданы).
        params = self.params if params is None else params
        query = urlencode(params) if params else ""
        if query:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"

        self.logger.debug( f"load url: {url}")
        with urlopen(url, timeout=60) as r:
            text = r.read().decode("utf-8", errors="replace")
        self.logger.debug( f"load done: {url} bytes={len(text)}")
        self.logger.debug( f"html: {text}")

        return text

    # Загрузка и парсинг страницы
    def load_html_and_parse(self, params=None) -> dict:
        """Загружает HTML со всех self.urls и возвращает словарь станций с пролетами.

        Пример:
            Вход: params={"t0": "2026-01-24", "t1": "2026-01-25"}
            Выход: {"R4.6S_Anadyr": {("log_view/..", "log_get/..")}}
        """
        passes = {}
        for url in self.urls:
            html = self.load_html(url, params=params)
            # Собираем станции в порядке на странице и ссылки на пролеты по станциям.
            self.logger.debug(f"parse page: base_url={url}, html_size={len(html)}")
            local = []
            for match in self.station_re.finditer(html):
                station = match.group(1)
                if station not in local:
                    local.append(station)

            for station in local:
                passes.setdefault(station, set())

            for row in self.date_row_re.finditer(html):
                cells = self.td_re.findall(row.group(2))
                for i, cell in enumerate(cells):
                    if i >= len(local):
                        break
                    station = local[i]
                    for p in self.pass_re.finditer(cell):
                        passes[station].add((
                            urljoin(url, p.group(1)),
                            urljoin(url, p.group(2)),
                        ))

        self.data_passes = passes
        return self.data_passes


    def get_station_list(self, start_day=None, end_day=None) -> list:
        """Возвращает список станций за диапазон дат.

        Если даты не заданы, используется текущая дата.

        Пример:
            Вход: start_day="2026-01-24", end_day="2026-01-25"
            Выход: ["R4.6S_Anadyr", ...]
        """
        stations = sorted(list(self.data_passes.keys()))
        self.logger.info(f"stations {stations}")
        self.logger.debug( f"stations found: {len(stations)}")
        return stations


    def print_station_list(self, start_day=None, end_day=None) -> None:
        """Печатает список станций за диапазон дат.

        Пример:
            Вход: start_day="2026-01-24", end_day="2026-01-25"
            Выход (stdout):
                R4.6S_Anadyr
                R4.7S_Omsk
        """
        stations = self.get_station_list(start_day, end_day)
        for station in stations:
            print(station)


    def get_passes(self, station: str, start_day=None, end_day=None) -> list:
        """Возвращает список пролетов по станции за диапазон дат.

        Если даты не заданы, используется текущая дата.

        Пример:
            Вход: station="R4.6S_Anadyr", start_day="2026-01-24", end_day="2026-01-25"
            Выход: [("log_view/..", "log_get/..")]
        """
        passes = self.data_passes
        if station in passes:
            result = sorted(passes[station])
            self.logger.debug( f"passes exact match: station={station} passes={result}")
            return result

        self.logger.debug( f"passes not found: station={station}")
        return []


    def print_passes(self, station: str, start_day=None, end_day=None) -> None:
        """Печатает список пролетов по станции за диапазон дат.

        Пример:
            Вход: station="R4.6S_Anadyr", start_day="2026-01-24", end_day="2026-01-25"
            Выход (stdout):
                log_view/... log_get/...
        """
        passes = self.get_passes(station, start_day, end_day)
        for view_url, get_url in passes:
            print(f"{view_url} {get_url}")


    def download_logs_file(self, passes_to_download: list, out_dir: str = "C:\\Users\\Yarik\\YandexDisk\\Engineering_local\\Soft\\GroundLinkMonitorServer\\passes_logs", max_parallel: int = 10) -> list:
        """Скачивает список пролетов и возвращает результаты.

        Принимает список пар (view_url, get_url) или (get_url, out_dir).

        Пример:
            Вход: passes_to_download=[("log_view/..", "log_get/..")], out_dir="logs/R4.6S_Anadyr"
            Выход: ["logs/R4.6S_Anadyr/filename.log"]
        """
        os.makedirs(out_dir, exist_ok=True)
        tasks = []
        date_re = re.compile(r"(\d{8})")
        station_re = re.compile(r"([^/\\\\]+?)__\d{8}")
        for item in passes_to_download:
            if len(item) == 2:
                view_or_get, second = item
                if second.startswith("http"):
                    get_url = second
                    date_match = date_re.search(get_url)
                    station_match = station_re.search(get_url)
                    station_name = station_match.group(1) if station_match else "unknown_station"
                    if date_match:
                        date_str = date_match.group(1)
                        date_dir = os.path.join(out_dir, date_str[0:4], date_str[4:6], date_str[6:8], station_name)
                        os.makedirs(date_dir, exist_ok=True)
                        tasks.append((get_url, date_dir))
                    else:
                        date_dir = os.path.join(out_dir, "unknown", "unknown", "unknown", station_name)
                        os.makedirs(date_dir, exist_ok=True)
                        self.logger.warning(f"date not found in url, using: {date_dir}")
                        tasks.append((get_url, date_dir))
                else:
                    get_url = view_or_get
                    date_match = date_re.search(get_url)
                    station_match = station_re.search(get_url)
                    station_name = station_match.group(1) if station_match else "unknown_station"
                    if date_match:
                        date_str = date_match.group(1)
                        date_dir = os.path.join(out_dir, date_str[0:4], date_str[4:6], date_str[6:8], station_name)
                        os.makedirs(date_dir, exist_ok=True)
                        tasks.append((get_url, date_dir))
                    else:
                        date_dir = os.path.join(out_dir, "unknown", "unknown", "unknown", station_name)
                        os.makedirs(date_dir, exist_ok=True)
                        self.logger.warning(f"date not found in url, using: {date_dir}")
                        tasks.append((get_url, date_dir))
            else:
                raise ValueError("passes_to_download items must be (view_url, get_url) or (get_url, out_dir)")

        results = asyncio.run(self._download_logs_async(tasks, max_parallel=max_parallel))
        for result in results:
            if isinstance(result, Exception):
                self.logger.exception("download failed", exc_info=result)
        return results



if __name__ == "__main__":
    # Локальный тест: проверяем основные функции класса.
    from Logger import Logger

    # Логгер пишет в файл и консоль; уровень debug нужен для подробных трассировок.
    logger = Logger(path_log="eus_downloader", log_level="debug")

    # Инициализируем портал с логгером.
    portal = EusLogPortal(logger=logger)

    # Диапазон дат: один день (end_day строго +1).
    start_day = datetime.now(timezone.utc).date()
    end_day = start_day + timedelta(days=1)
    params = portal._build_date_params("2025-12-01", end_day)

    # Тест load_html.
    # html = portal.load_html(portal.urls[0], params=params)
    # portal.logger.info(f"html length: {len(html)}")

    # Тест load_and_parse.
    page_passes = portal.load_html_and_parse(params=params)
    portal.logger.info(f"stations in page: {len(page_passes)}")
    portal.logger.debug(f"page_passes: {page_passes}")

    # Тест get_station_list.
    # station_list = portal.get_station_list(start_day, end_day)
    # portal.logger.info(f"OK: {station_list}")

    # Тест download_logs_file
    for station in portal.get_station_list(start_day, end_day):
        passes = portal.get_passes(station, start_day, end_day)
        portal.logger.info(f"passes for {station}: {len(passes)}")
        if passes:
            results = portal.download_logs_file(passes, out_dir="C:\\Users\\Yarik\\YandexDisk\\Engineering_local\\Soft\\GroundLinkMonitorServer\\passes_logs")
            ok = sum(1 for r in results if isinstance(r, str))
            fail = sum(1 for r in results if isinstance(r, Exception))
            portal.logger.info(f"download_logs_file for {station}: ok={ok}, fail={fail}")

