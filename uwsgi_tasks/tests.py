# -*- coding: utf-8 -*-
import mock
from datetime import timedelta
from unittest import TestCase

from uwsgi_tasks.utils import import_by_path, get_function_path
from uwsgi_tasks.tasks import (
    RuntimeTask, manage_mule_request, manage_spool_request
)
from uwsgi_tasks import task, TaskExecutor


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


storage = mock.MagicMock()


@task(executor=TaskExecutor.MULE)
def mule_task(a, b, c=3, d=4):
    storage(a, b, c, d)


@task(executor=TaskExecutor.SPOOLER, priority=10, at=timedelta(seconds=10))
def spooler_task(a, b=None):
    storage(a, b)


class TaskTest(TestCase):

    def setUp(self):
        self.storage = storage
        self.storage.reset_mock()
        self.patcher = mock.patch('uwsgi_tasks.tasks.uwsgi')

    def test_runtime_task_execution(self):

        def runtime_task(a, b, c=None):
            self.storage(a, b, c=c)

        runtime_task = RuntimeTask(runtime_task)

        runtime_task(1, 2, c=3)
        self.storage.assert_called_once_with(1, 2, c=3)

        runtime_task(4, '2')
        self.storage.assert_called_with(4, '2', c=None)

        with self.assertRaises(TypeError):
            runtime_task()

    def test_task_is_not_executed_if_uwsgi_not_available(self):
        result = mule_task()
        self.assertFalse(result)
        self.assertFalse(self.storage.called)

    def test_mule_task_execution_became_runtime(self):
        # 'mule' not in uwsgi.opt, so it goes to runtime
        with self.patcher:
            mule_task(4, 7, 8)

        self.storage.assert_called_with(4, 7, 8, 4)

    def test_mule_task_execution(self):
        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'mule': 1}
            m_task = mule_task(6, 6, u'a', u'кириллический')

            message = m_task.get_message_content()
            uwsgi_mock.mule_msg.assert_called_with(
                message, m_task.default_mule_id
            )
            manage_mule_request(message)

        self.storage.assert_called_once_with(6, 6, u'a', u'кириллический')

    def test_spooler_task_execution(self):
        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'spooler': '/tmp/spooler'}
            uwsgi_mock.SPOOL_OK = 1

            s_task = spooler_task(0, '1')
            message = s_task.get_message_content()

            uwsgi_mock.spool.assert_called_with(message)
            manage_spool_request(message)

        self.storage.assert_called_once_with(0, '1')