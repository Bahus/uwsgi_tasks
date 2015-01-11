# -*- coding: utf-8 -*-
from unittest import TestCase

from uwsgi_tasks.utils import import_by_path, get_function_path


def local_function(value):
    return value ** 2


class UtilsTest(TestCase):

    def test_import_by_path(self):
        with self.assertRaises(ImportError):
            import_by_path('len')

        with self.assertRaises(ImportError):
            import_by_path('uwsgi_tasks.tests.unknown_function')

        main = __import__('__main__')
        main.local_function = local_function

        self.assertTrue(local_function is import_by_path('local_function'))

        self.assertTrue(
            local_function is import_by_path('uwsgi_tasks.tests.local_function')
        )

        self.assertTrue(len is import_by_path('__builtin__.len'))

    def test_get_function_path(self):
        self.assertEquals(
            get_function_path(local_function),
            'uwsgi_tasks.tests.local_function'
        )

        self.assertEqual(
            get_function_path(len),
            '__builtin__.len'
        )

        def nested():
            pass

        self.assertEquals(
            get_function_path(nested),
            'uwsgi_tasks.tests.nested'
        )