# -*- coding: utf-8 -*-
import six
from importlib import import_module


def import_by_path(dotted_path):
    """Import a dotted module path and return the attribute/class designated
    by the last name in the path. Raise ImportError if the import failed.

    Adapted from Django 1.7
    """

    try:
        if not dotted_path.count('.'):
            dotted_path = '.'.join(['__main__', dotted_path])

        module_path, class_name = dotted_path.rsplit('.', 1)
    except ValueError:
        msg = '"{}" doesn\'t look like a module path'.format(dotted_path)
        raise ImportError(msg)

    try:
        module = import_module(module_path)
    except ImportError as ex:
        raise ImportError('Failed to import "{}" - {}'.format(dotted_path, ex))

    try:
        return getattr(module, class_name)
    except AttributeError:
        msg = 'Module "{}" does not define a "{}" attribute/class'.format(
            dotted_path, class_name)
        raise ImportError(msg)


def get_function_path(function):
    """Get received function path (as string), to import function later
    with `import_string`.
    """
    if isinstance(function, six.string_types):
        return function

    func_path = []

    module = getattr(function, '__module__', '__main__')
    if module:
        func_path.append(module)

    func_path.append(function.__name__)
    return '.'.join(func_path)


def django_setup(settings_module=None):
    """Initialize Django if required, must be run before performing
    any task on spooler or mule.
    """
    from django.conf import settings, ENVIRONMENT_VARIABLE

    if settings.configured:
        return

    if settings_module:
        import os
        os.environ[ENVIRONMENT_VARIABLE] = settings_module

    try:
        # django > 1.7
        from django import setup
    except ImportError:
        # django < 1.7
        def setup():
            settings._setup()

    setup()


class ProxyDict(dict):

    def __init__(self, dict_instance, key):
        super(ProxyDict, self).__init__()

        self.key = key

        if self.key in dict_instance:
            self.update(dict_instance[self.key])

        dict_instance[self.key] = self
