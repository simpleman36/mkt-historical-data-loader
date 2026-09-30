import logging
import sys
import os
import time
from typing import Optional

_LOGGER: Optional[logging.Logger] = None

def get_logger(name: str = "app") -> logging.Logger:
    """
    Global logger:
      - logs to console (stdout) and a file
      - filename includes current date + time (YYYYMMDDHHmm), e.g., logs/mkt-data-loader-202508221430.log
      - file is opened immediately (not delayed) so logs write even if script is aborted
      - includes module, function, and line in each record
      - children via get_logger("module_x") inherit handlers
    """
    global _LOGGER
    if _LOGGER is None:
        os.makedirs("logs", exist_ok=True)

        # Build dated filename with hours and minutes
        now_str = time.strftime("%Y%m%d%H%M")  # YYYYMMDDHHmm
        log_path = os.path.join("logs", f"mkt-data-loader-{now_str}.log")

        # Create empty file if it doesn't exist (ensures immediate write capability)
        try:
            with open(log_path, "a", encoding="utf-8"):
                pass
        except OSError:
            # If directory/file issues happen, fall back to stdout-only
            log_path = None

        logger = logging.getLogger("app")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        formatter = logging.Formatter(
            fmt="%(asctime)s :: %(levelname)s | %(module)s.%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y%m%d::%H:%M:%S",  # yyyymmdd::hh:mm:ss
        )

        # Custom handler that flushes on each log message
        class FlushHandler(logging.Handler):
            def __init__(self, stream=None, filepath=None):
                super().__init__()
                if stream:
                    self.stream = stream
                    self.filepath = None
                else:
                    self.stream = None
                    self.filepath = filepath
                    self.file = open(filepath, "a", encoding="utf-8")

            def emit(self, record):
                try:
                    msg = self.format(record)
                    if self.stream:
                        self.stream.write(msg + "\n")
                        self.stream.flush()
                    else:
                        self.file.write(msg + "\n")
                        self.file.flush()
                except Exception:
                    self.handleError(record)

            def close(self):
                if hasattr(self, "file") and self.file:
                    self.file.close()
                super().close()

        # Console handler with immediate flush on each write
        sh = FlushHandler(stream=sys.stdout)
        sh.setFormatter(formatter)
        sh.setLevel(logging.DEBUG)
        logger.addHandler(sh)

        # File handler (opened immediately, flushed on each write)
        if log_path:
            fh = FlushHandler(filepath=log_path)
            fh.setFormatter(formatter)
            fh.setLevel(logging.DEBUG)
            logger.addHandler(fh)

        _LOGGER = logger

    return _LOGGER if name == "app" else _LOGGER.getChild(name)  # type: ignore[return-value]