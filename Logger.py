import logging
import coloredlogs
from datetime import datetime  


class Logger:
    '''Класс отвечающий за логирование. Логи пишуться в файл, так же выводться в консоль'''

    def __init__(self,config):
        log_level = {'debug':logging.DEBUG,
                     'info':logging.INFO,
                     'warning':logging.WARNING,
                     'critical':logging.CRITICAL,
                     'error':logging.ERROR}
                     
        self.logs = logging.getLogger(__name__)
        self.logs.setLevel(log_level[config['log_level']])

        # название файла 
        name = config['path_log'] + '-'.join('-'.join('-'.join(str(datetime.now()).split()).split('.')).split(':')) + '.log'

        # обработчик записи в лог-файл
        self.file = logging.FileHandler(name)
        self.fileformat = logging.Formatter("%(asctime)s : %(levelname)s : %(message)s")
        self.file.setLevel(log_level[config['log_level']])
        self.file.setFormatter(self.fileformat)

        # обработчик вывода в консоль лог файла
        self.stream = logging.StreamHandler()
        self.streamformat = logging.Formatter(
            "%(levelname)s:%(module)s:%(message)s")
        self.stream.setLevel(log_level[config['log_level']])
        self.stream.setFormatter(self.streamformat)

        # инициализация обработчиков
        self.logs.addHandler(self.file)
        self.logs.addHandler(self.stream)
        coloredlogs.install(level=log_level[config['log_level']], logger=self.logs, fmt='%(asctime)s : %(levelname)s : %(message)s')

        self.logs.info('Start logging') 

    def debug(self, message):
        self.logs.debug(message)

    def info(self, message):
        self.logs.info(message)

    def warning(self, message):
        self.logs.warning(message)

    def critical(self, message):
        self.logs.critical(message)

    def error(self, message):
        self.logs.error(message)