# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest import TestCase

import os

try:
    import mock     # Python 2
except ImportError:
    from unittest import mock    # Python 3

import six
from uwsgi_tasks.utils import import_by_path, get_function_path
from uwsgi_tasks.tasks import (
    RuntimeTask, manage_mule_request, manage_spool_request, TimerTask,
    get_current_task, SpoolerTask, serialize
)
from uwsgi_tasks import task, TaskExecutor, RetryTaskException, SPOOL_OK


def local_function(value):
    return value ** 2


class UtilsTest(TestCase):
    builtin_module = '__builtin__' if six.PY2 else 'builtins'

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
        self.assertTrue(len is import_by_path('{}.len'.format(
            self.builtin_module)))

    def test_get_function_path(self):
        self.assertEqual(
            get_function_path(local_function),
            'uwsgi_tasks.tests.local_function'
        )

        self.assertEqual(
            get_function_path(len),
            '{}.len'.format(self.builtin_module)
        )

        def nested():
            pass

        self.assertEqual(get_function_path(nested), 'uwsgi_tasks.tests.nested')


storage = mock.MagicMock()


@task(executor=TaskExecutor.MULE)
def mule_task(a, b, c='3', d='4'):
    storage(a, b, c, d)


@task(executor=TaskExecutor.SPOOLER, priority='10', at=timedelta(seconds=10))
def spooler_task(a, b=None):
    storage(a, b)


@task(executor=TaskExecutor.SPOOLER, retry_count=2)
def spooler_retry_task(g, h):
    storage(g, h)
    raise RetryTaskException(timeout=10)


@task(executor=TaskExecutor.SPOOLER, retry_count=2,
      retry_timeout=timedelta(seconds=20))
def spooler_and_task_introspection(a, b):
    current_task = get_current_task()
    assert isinstance(current_task, SpoolerTask)
    assert current_task.executor == TaskExecutor.SPOOLER

    if len(current_task.buffer):
        assert current_task.retry_count == 1
        assert current_task.retry_timeout == 50
        assert current_task.at == timedelta(seconds=50)
        assert len(current_task.buffer) == 1
        assert current_task.buffer[b'message'] == u'Hello, World!'
    else:
        assert current_task.executor == TaskExecutor.SPOOLER
        assert current_task.retry_count == 2
        assert current_task.retry_timeout == 20

    storage(a, b)
    current_task.buffer[b'message'] = u'Hello, World!'
    raise RetryTaskException(timeout=50)


SPOOLER_DIR = os.path.expanduser('~')


@task(executor=TaskExecutor.SPOOLER)
def spooler_task_with_cwd_changed():
    storage()

    current_dir = os.getcwd()
    assert current_dir != SPOOLER_DIR


@task(executor=TaskExecutor.SPOOLER, working_dir=None)
def spooler_task_with_cwd_not_changed():
    storage()

    current_dir = os.getcwd()
    assert current_dir == SPOOLER_DIR


def timer_task(signum):
    storage(signum)


@task(executor=TaskExecutor.RUNTIME)
def runtime_task(*args, **kwargs):
    return True


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
        mule_task(4, 5, '6', '7')
        self.storage.assert_called_once_with(4, 5, '6', '7')

    def test_mule_task_execution_became_runtime(self):
        # 'mule' not in uwsgi.opt, so it goes to runtime
        with self.patcher:
            mule_task(4, 7, '8')

        self.storage.assert_called_with(4, 7, '8', '4')

    def test_mule_task_execution(self):
        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'mule': 1}
            m_task = mule_task(6, 6,  u'a', u'кириллический')

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

            self.assertEqual(message[b'priority'], b'10')
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

            self.assertFalse(message.get(b'at'))

            uwsgi_mock.spool.assert_called_with(message)

            manage_spool_request(message)
            self.storage.assert_called_once_with(666, 777)
            self.assertEqual(2, uwsgi_mock.spool.call_count)

            new_message = uwsgi_mock.spool.call_args_list[-1][0][0]
            self.assertTrue(new_message.get(b'at'))

            manage_spool_request(message)

        self.assertEqual(2, self.storage.call_count)

    def test_spooler_task_introspection(self):
        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'spooler': '/tmp/spooler'}

            s_task = spooler_and_task_introspection('a', 1)
            message = s_task.get_message_content()
            manage_spool_request(message)

            new_message = uwsgi_mock.spool.call_args_list[-1][0][0]
            manage_spool_request(new_message)

        self.storage.assert_called_with('a', 1)
        self.assertEqual(2, self.storage.call_count)

    def test_spooler_task_large_args_in_body(self):
        large_arg = 'b' * 64 * 1024
        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'spooler': '/tmp/spooler'}
            s_task = spooler_task('a', large_arg)
            message = s_task.get_message_content()

            expected_args = s_task._encode_message({
                'args': serialize(()),
                'kwargs': serialize({}),
            })

            self.assertEqual(message[b'args'], expected_args[b'args'])
            self.assertEqual(message[b'kwargs'], expected_args[b'kwargs'])
            manage_spool_request(message)

        self.storage.assert_called_once_with('a', large_arg)

    def test_task_working_directory_changed(self):
        current_dir = os.getcwd()

        with self.patcher as uwsgi_mock:
            uwsgi_mock.opt = {'spooler': SPOOLER_DIR}

            s_task = spooler_task_with_cwd_changed()
            message = s_task.get_message_content()
            os.chdir(SPOOLER_DIR)
            manage_spool_request(message)
            self.assertTrue(self.storage.called)
            self.storage.reset_mock()

            os.chdir(current_dir)

            s_task = spooler_task_with_cwd_not_changed()
            message = s_task.get_message_content()
            os.chdir(SPOOLER_DIR)
            manage_spool_request(message)
            self.assertTrue(self.storage.called)
            self.storage.reset_mock()

        os.chdir(current_dir)

    def test_runtime_task_pickling(self):
        runtime_task('args', {'kw': 'args'})
        with self.assertRaises(Exception):
            runtime_task(lambda x: x, {'kw': 'args'})
        with self.assertRaises(Exception):
            runtime_task('args', {'kw': lambda x: x})
