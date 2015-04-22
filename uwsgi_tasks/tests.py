# -*- coding: utf-8 -*-
import mock
from datetime import timedelta
from unittest import TestCase

from uwsgi_tasks.utils import import_by_path, get_function_path
from uwsgi_tasks.tasks import (
    RuntimeTask, manage_mule_request, manage_spool_request, TimerTask
)
from uwsgi_tasks import task, TaskExecutor, RetryTaskException, SPOOL_OK


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


@task(executor=TaskExecutor.SPOOLER, retry_count=2)
def spooler_retry_task(g, h):
    storage(g, h)
    raise RetryTaskException(timeout=10)


def timer_task(signum):
    storage(signum)


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

    def test_task_is_executed_at_runtime_if_uwsgi_not_available(self):
        mule_task(4, 5, 6, 7)
        self.storage.assert_called_once_with(4, 5, 6, 7)

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
            uwsgi_mock.SPOOL_OK = SPOOL_OK

            s_task = spooler_task(0, '1')
            message = s_task.get_message_content()

            self.assertEqual(message['priority'], 10)
            uwsgi_mock.spool.assert_called_with(message)
            manage_spool_request(message)

        self.storage.assert_called_once_with(0, '1')

    def test_rb_timer_task_execution(self):
        with self.patcher as uwsgi_mock:
            t_task = TimerTask(
                timer_task,
                signal=10,
                seconds=5,
                iterations=3,
                target='workers'
            )

            t_task()

            uwsgi_mock.register_signal.assert_called_once_with(
                10, 'workers', t_task.signal_handler
            )
            uwsgi_mock.add_rb_timer.assert_called_once_with(10, 5, 3)

            t_task.signal_handler(10)
            self.storage.assert_called_once_with(10)

    def test_spooler_retry_on_exception(self):
        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'spooler': '/tmp/spooler'}
            uwsgi_mock.SPOOL_OK = SPOOL_OK

            s_task = spooler_retry_task(666, 777)
            message = s_task.get_message_content()
            self.assertFalse(message.get('at'))

            uwsgi_mock.spool.assert_called_with(message)
            manage_spool_request(message)
            self.storage.assert_called_once_with(666, 777)

            new_message = uwsgi_mock.spool.call_args_list[-1][0][0]
            self.assertTrue(new_message.get('at'))

            manage_spool_request(message)

        self.assertEqual(2, self.storage.call_count)