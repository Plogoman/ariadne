#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .compat import __program__
from .options import options
from .display import CustomFormatter

## Add TRACE logging level
TRACE_LEVEL_NUM = 25
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")
logging.TRACE = TRACE_LEVEL_NUM

def trace(self, message, *args, **kwargs):
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kwargs)
logging.Logger.trace = trace


## Setup Logging Handlers
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(CustomFormatter())
stdout_handler.terminator = ''

file_handler = logging.FileHandler(options.logfile, encoding='utf-8')
file_handler.setFormatter(CustomFormatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
file_handler.setLevel(logging.INFO)
file_handler.terminator = ''

debug_file_handler = logging.FileHandler(options.debug_logfile, encoding='utf-8')
debug_file_handler.setFormatter(CustomFormatter("%(asctime)s %(message)s"))
debug_file_handler.addFilter(lambda record: True if record.levelno == logging.DEBUG else False)
debug_file_handler.terminator = ''


## Initialize Loggers
logger = logging.getLogger(__program__)
logger.addHandler(stdout_handler)
logger.addHandler(file_handler)
logger.addHandler(debug_file_handler)

cmdlogger = logging.getLogger(f"{__program__}_cmd")
cmdlogger.setLevel(logging.INFO)
cmdlogger.addHandler(stdout_handler)
