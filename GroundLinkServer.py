import argparse
import os
import re
import time
from datetime import datetime, timedelta, timezone
from Logger import Logger
from EusLogDownloader import EusLogDownloader
from DbManager import DbManager
from PassAnalyzer import PassAnalyzer
from SatPass import SatPas

class GroundLinkServer:
    
    def __init__(self) -> None:
        # Инициализация основного логера 
        self.logger = Logger(path_log="C:\\Users\\Yarik\\YandexDisk\\Engineering_local\\Soft\\GroundLinkMonitorServer\\server_logs\\ground_link_server-", log_level="info", logger_name="MAIN")
        
        # инициализация обработчика базы данных 
        self.logger_db = Logger(path_log="C:\\Users\\Yarik\\YandexDisk\\Engineering_local\\Soft\\GroundLinkMonitorServer\\server_logs\\ground_link_db-", log_level="info", logger_name="DB")
        self.db_manager = DbManager(logger=self.logger_db)

        # uнициализация загрузчика лог файлов 
        self.logger_eus = Logger(path_log="C:\\Users\\Yarik\\YandexDisk\\Engineering_local\\Soft\\GroundLinkMonitorServer\\server_logs\\ground_link_eus-", log_level="debug", logger_name="EUS")
        self.eus = EusLogDownloader(logger=self.logger_eus)

        # инициализация анализатора логов
        self.logger_analyzer = Logger(path_log="C:\\Users\\Yarik\\YandexDisk\\Engineering_local\\Soft\\GroundLinkMonitorServer\\server_logs\\ground_link_analyzer-", log_level="debug", logger_name="ANALYZER")
        self.analyzer = PassAnalyzer(logger=self.logger_analyzer)
        
    
    def _parse_log_metadata(self, path: str):
        base = os.path.basename(path)
        station_name = None
        if "__" in base:
            station_name, rest = base.split("__", 1)
        else:
            rest = base

        match = re.match(r"(?P<date>\d{8})_(?P<time>\d{6})_(?P<sat>.+)_rec\d*\.log$", rest)
        if not match:
            return None

        pass_date = datetime.strptime(match.group("date"), "%Y%m%d").date()
        pass_time = datetime.strptime(match.group("time"), "%H%M%S").time()
        satellite_name = match.group("sat")
        return station_name, satellite_name, pass_date, pass_time

    def print_log_daily_stats(self, stat_day) -> None:
        """Печатает статистику успешных пролетов за день."""
        try:
            station_rows = self.db_manager.get_daily_station_stats(stat_day)
        except Exception as exc:
            self.logger.exception("daily stats query failed", exc_info=exc)
            return
        if not station_rows:
            self.logger.info(f"no stats for day: {stat_day}")
            return

        stations = [row[0] for row in station_rows]
        snr_stats = {station: {"avg": row[5], "max": None, "file": None} for station, *row in station_rows}

        max_passes = []
        try:
            max_passes = self.db_manager.get_max_sum_snr_passes(stat_day)
        except Exception as exc:
            self.logger.exception("max sum snr query failed", exc_info=exc)
            max_passes = []

        if max_passes:
            analyzed_max = self.analyzer.analyze_passes(max_passes)
            for sat_pass in analyzed_max:
                entry = snr_stats.get(sat_pass.station_name)
                if entry is None:
                    continue
                if sat_pass.max_snr is not None:
                    entry["max"] = sat_pass.max_snr
                    entry["file"] = os.path.basename(sat_pass.log_path or "")

        self.logger.info("ИТОГОВАЯ СВОДКА ПО ВСЕМ СТАНЦИЯМ")
        self.logger.info("")
        self.logger.info(
            "Станция                 Всего   Успешных   Неуспешных   % неуспешных   Средний SNR"
        )
        self.logger.info("-" * 80)

        total_all = 0
        success_all = 0
        failed_all = 0
        total_snr = 0.0
        snr_count = 0

        for station_name, total, success, failed, failed_percent, avg_snr in station_rows:
            avg_snr = avg_snr or 0.0
            if avg_snr:
                total_snr += avg_snr
                snr_count += 1
            total_all += total
            success_all += success
            failed_all += failed
            self.logger.info(
                f"{station_name:<23} {total:>5} {success:>10} {failed:>12} "
                f"{failed_percent:>11.1f}% {avg_snr:>13.2f}"
            )

        failed_percent_all = round((failed_all * 100.0) / total_all, 1) if total_all else 0.0
        avg_snr_all = round(total_snr / snr_count, 2) if snr_count else 0.0
        self.logger.info("-" * 80)
        self.logger.info(
            f"{'ВСЕГО':<23} {total_all:>5} {success_all:>10} {failed_all:>12} "
            f"{failed_percent_all:>11.1f}% {avg_snr_all:>13.2f}"
        )

        self.logger.info("")
        self.logger.info("ФАЙЛЫ С МАКСИМАЛЬНЫМ SNR ПО СТАНЦИЯМ")
        self.logger.info("")
        self.logger.info("Станция             Файл с макс. SNR                                      SNR")
        self.logger.info("-" * 80)

        for station_name in stations:
            entry = snr_stats.get(station_name, {})
            max_snr = entry.get("max")
            max_file = entry.get("file")
            if max_snr is None or max_snr == 0:
                self.logger.info(f"{station_name:<20} {'станция не работает'}")
            else:
                self.logger.info(f"{station_name:<20} {max_file:<55} {max_snr:>6.2f}")

    def main(
        self,
        start_day=None,
        end_day=None,
        off_email: bool = False,
        debug_email: bool = False,
        ):
            
        # Заглушки флагов почты.
        if off_email:
            self.logger.info("email disabled by flag")

        if debug_email:
            self.logger.info("debug email enabled by flag")

        if start_day is None and end_day is None:
            params = None

        else:
            if start_day is None:
                start_day = end_day
            if end_day is None:
                end_day = start_day + timedelta(days=1)
            start_dt = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
            end_dt = datetime.combine(end_day, datetime.min.time(), tzinfo=timezone.utc)
            params = (start_dt, end_dt)

        try:
            page_passes = self.eus.load_html_and_parse(params=params)

        except TimeoutError as exc:
            self.logger.warning(f"load_html_and_parse timeout: {exc}")
            return

        except Exception as exc:
            self.logger.exception("load_html_and_parse failed", exc_info=exc)
            return

        self.logger.info(len(page_passes))

        # Скачиваем все логи и добавляем пролеты в БД.
        pass_items = []
        for _station, passes in page_passes.items():
            pass_items.extend(passes)

        if pass_items:
            results = self.eus.download_logs_file(pass_items)
            analyzed = self.analyzer.analyze_passes(results)

            for sat_pass in analyzed:

                if not sat_pass.log_path:
                    self.logger.warning(f"log download failed for {sat_pass.station_name}")
                    continue

                station_name = sat_pass.station_name
                satellite_name = sat_pass.satellite_name
                pass_date = sat_pass.pass_date
                pass_time = sat_pass.pass_start_time.time() if sat_pass.pass_start_time else None

                if not (station_name and satellite_name and pass_date and pass_time):
                    meta = self._parse_log_metadata(sat_pass.log_path)

                    if meta:
                        station_from_name, satellite_from_name, meta_date, meta_time = meta
                        station_name = station_name or station_from_name
                        satellite_name = satellite_name or satellite_from_name
                        pass_date = pass_date or meta_date
                        pass_time = pass_time or meta_time

                if not (station_name and satellite_name and pass_date and pass_time):

                    self.logger.warning(f"cannot parse log metadata: {sat_pass.log_path}")
                    continue

                sat_pass.station_name = station_name
                sat_pass.satellite_name = satellite_name
                sat_pass.pass_date = pass_date

                if sat_pass.pass_start_time is None:
                    sat_pass.pass_start_time = datetime.combine(pass_date, pass_time)
                sat_pass.success = True

                self.db_manager.add_pass(sat_pass, is_commercial=False)

        stat_day = start_day or datetime.now(timezone.utc).date()
        self.print_log_daily_stats(stat_day)


if __name__ == "__main__":
    #
    parser = argparse.ArgumentParser()
    parser.add_argument("start_date", nargs="?", help="Дата начала (YYYYMMDD)")
    parser.add_argument("end_date", nargs="?", help="Дата завершения (YYYYMMDD)")
    parser.add_argument("--sch", action="store_true", help="Запуск в 00:00 UTC по расписанию")
    parser.add_argument("--off-email", action="store_true", help="Отключить отправку email")
    parser.add_argument("--debag-email", action="store_true", help="Отладочная отправка email")
    args = parser.parse_args()

    def parse_yyyymmdd(value: str):
        return datetime.strptime(value, "%Y%m%d").date()

    server = GroundLinkServer()

    start_day = parse_yyyymmdd(args.start_date) if args.start_date else None
    end_day = parse_yyyymmdd(args.end_date) if args.end_date else None

    if args.sch:
        while True:
            now = datetime.now(timezone.utc)
            next_midnight = datetime.combine(
                now.date() + timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )


            sleep_seconds = (next_midnight - now).total_seconds()
            if sleep_seconds > 0:
                server.logger.info(f"sleep until UTC midnight: {sleep_seconds:.0f}s")
                time.sleep(sleep_seconds)
            run_day = datetime.now(timezone.utc).date()

            server.main(
                start_day=run_day,
                end_day=run_day + timedelta(days=1),
                off_email=args.off_email,
                debug_email=args.debag_email,
            )

    else:
        server.main(
            start_day=start_day,
            end_day=end_day,
            off_email=args.off_email,
            debug_email=args.debag_email,
        )
