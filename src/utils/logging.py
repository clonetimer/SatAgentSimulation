"""Unified logging and exception handling utilities for satellite simulation."""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any, Callable, Generator, Optional


class SimulationError(Exception):
    """Base exception for satellite simulation errors."""

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.cause = cause


class ConfigurationError(SimulationError):
    """Raised when configuration is invalid."""


class RuntimeSimulationError(SimulationError):
    """Raised when simulation encounters a runtime error."""


class DataUnavailableError(SimulationError):
    """Raised when required data files are unavailable."""


def configure_logging(
    level: int = logging.INFO,
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    file_path: Optional[str] = None,
) -> logging.Logger:
    """Configure unified logging for satellite simulation.

    Parameters
    ----------
    level : int
        Logging level (default: logging.INFO)
    format : str
        Log message format (default: "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_path : Optional[str]
        If provided, also log to this file

    Returns
    -------
    logging.Logger
        Root logger with configured handlers
    """
    logger = logging.getLogger()
    logger.setLevel(level)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    formatter = logging.Formatter(format)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if file_path:
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name.

    Parameters
    ----------
    name : str
        Logger name, typically __name__

    Returns
    -------
    logging.Logger
        Configured logger instance
    """
    return logging.getLogger(name)


@contextmanager
def simulation_error_handler(logger: logging.Logger, re_raise: bool = True) -> Generator[None, None, None]:
    """Context manager for unified simulation error handling.

    Parameters
    ----------
    logger : logging.Logger
        Logger to use for error reporting
    re_raise : bool
        If True, re-raise the exception after logging (default: True)

    Yields
    ------
    None
    """
    try:
        yield
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}", exc_info=True)
        if re_raise:
            raise
    except DataUnavailableError as e:
        logger.error(f"Data unavailable: {e}", exc_info=True)
        if re_raise:
            raise
    except RuntimeSimulationError as e:
        logger.error(f"Runtime simulation error: {e}", exc_info=True)
        if re_raise:
            raise
    except SimulationError as e:
        logger.error(f"Simulation error: {e}", exc_info=True)
        if re_raise:
            raise
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        if re_raise:
            raise RuntimeSimulationError(f"Unexpected error: {e}", cause=e)


def safe_execute(
    func: Callable[..., Any],
    logger: logging.Logger,
    *args: Any,
    default: Any = None,
    **kwargs: Any,
) -> Any:
    """Execute a function with unified error handling.

    Parameters
    ----------
    func : Callable[..., Any]
        Function to execute
    logger : logging.Logger
        Logger to use for error reporting
    *args : Any
        Positional arguments to pass to func
    default : Any
        Default value to return if execution fails
    **kwargs : Any
        Keyword arguments to pass to func

    Returns
    -------
    Any
        Result of func(*args, **kwargs) or default if failed
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Failed to execute {func.__name__}: {e}", exc_info=True)
        return default


__all__ = [
    "SimulationError",
    "ConfigurationError",
    "RuntimeSimulationError",
    "DataUnavailableError",
    "configure_logging",
    "get_logger",
    "simulation_error_handler",
    "safe_execute",
]
