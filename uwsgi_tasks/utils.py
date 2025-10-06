from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Optional


def import_by_path(dotted_path: str) -> Any:
    """Import a dotted module path and return the attribute/class designated
    by the last name in the path. Raise ImportError if the import failed.

    Adapted from Django.
    """
    try:
        if '.' not in dotted_path:
            dotted_path = f'__main__.{dotted_path}'

        module_path, class_name = dotted_path.rsplit('.', 1)
    except ValueError as e:
        raise ImportError(f'"{dotted_path}" doesn\'t look like a module path') from e

    try:
        module = import_module(module_path)
    except ImportError as e:
        raise ImportError(f'Failed to import "{dotted_path}" - {e}') from e

    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ImportError(f'Module "{module_path}" does not define a "{class_name}" attribute/class') from e


def get_function_path(function: Callable[..., Any] | str) -> str:
    """Get the import path for a function as a string."""
    if isinstance(function, str):
        return function

    module = getattr(function, '__module__', '__main__')
    if module:
        return f'{module}.{function.__name__}'

    return function.__name__


def django_setup(settings_module: Optional[str] = None) -> None:
    """Initialize Django if required, must be run before performing
    any task on a spooler or mule.
    """
    from django.conf import settings, ENVIRONMENT_VARIABLE
    import os

    if settings.configured:
        return

    if settings_module:
        os.environ[ENVIRONMENT_VARIABLE] = settings_module

    from django import setup
    setup()


class ProxyDict(dict):
    """A dict that proxies access to a nested dictionary within a parent dict."""

    def __init__(self, dict_instance: dict[str, Any], key: str):
        super().__init__()
        self.key = key

        if self.key in dict_instance:
            self.update(dict_instance[self.key])

        dict_instance[self.key] = self