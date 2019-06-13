# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import inspect
import collections
import logging
import threading
import traceback
import calendar
from contextlib import contextmanager
from datetime import datetime, timedelta

import os
import six
import warnings
from uwsgi_tasks.utils import (
    import_by_path,
    get_function_path,
    ProxyDict,
)


try:
    # noinspection PyPep8Naming
    from six.moves import cPickle as pickle
except ImportError:
    import pickle


try:
    import uwsgi
    if uwsgi.masterpid() == 0:
        warnings.warn('Uwsgi master process must be enabled', RuntimeWarning)
        uwsgi = None

    SPOOL_OK = uwsgi.SPOOL_OK
    SPOOL_IGNORE = uwsgi.SPOOL_IGNORE
    SPOOL_RETRY = uwsgi.SPOOL_RETRY
except ImportError:
    uwsgi = None
    SPOOL_OK = -2
    SPOOL_IGNORE = 0
    SPOOL_RETRY = -1


# maximum message size for mule and spooler (64Kb)
# see uwsgi settings `mule-msg-size`
UWSGI_MAXIMUM_MESSAGE_SIZE = 64 * 1024


# storage for saved tasks
# often it's not required since tasks will be imported by path
saved_tasks = threading.local()

# logger
logger = logging.getLogger('uwsgi_tasks')


def serialize(content):
    return pickle.dumps(content)


def deserialize(serialized):
    return pickle.loads(serialized)


def load_function(func_name):
    func = getattr(saved_tasks, func_name, None)
    if func:
        return func
    return import_by_path(func_name)


def manage_spool_request(message):
    logger.debug('Processing request for spooler')
    try:
        task = SpoolerTask.extract_from_message(message)
        if task:
            return task.execute_now()
    except:
        traceback.print_exc()
        logger.exception('Spooler message ignored: "%s"', message)
        return SPOOL_OK


def manage_mule_request(message):
    logger.debug('Processing request for mule')
    task = MuleTask.extract_from_message(message)
    if task:
        return task.execute_now()


def get_free_signal():
    if not uwsgi:
        return None

    for signum in range(0, 256):
        if not uwsgi.signal_registered(signum):
            return signum

    raise Exception('No free uwsgi signal available')


def set_uwsgi_callbacks():
    if uwsgi:
        uwsgi.spooler = manage_spool_request
        uwsgi.mule_msg_hook = manage_mule_request

set_uwsgi_callbacks()


def get_current_task():
    """ Introspection API: allows to get current task instance in
    called function.
    """
    stack = inspect.stack()
    try:
        caller_name = stack[1][3]
        caller_task = stack[1][0].f_globals[caller_name]
    except (IndexError, KeyError):
        logger.exception('get_current_task failed')
        return None

    return getattr(caller_task.function, BaseTask.attr_name, None)


class RetryTaskException(Exception):
    """ Throw this exception to re-execute spooler task """

    def __init__(self, count=None, timeout=None):
        self.count = count
        self.timeout = timeout


class TaskExecutor:
    AUTO = 1
    SPOOLER = 2
    MULE = 3
    RUNTIME = 4


class BaseTask(object):
    attr_name = 'uwsgi_task'
    executor = None

    def __init__(self, function, **setup):
        self._function = None

        if isinstance(function, six.string_types):
            self.function_name = function
        elif callable(function):
            self._function = function
            self.function_name = get_function_path(function)
        else:
            raise TypeError('Callable or dotted path must be provided '
                            'as first argument')

        self.args = setup.pop('args', ())
        self.kwargs = setup.pop('kwargs', {})
        self.setup = setup or {}
        self.setup.setdefault('working_dir', os.getcwd())
        self._buffer = None

    def __repr__(self):
        return u'<Task: {} for "{}" with args={!r}, kwargs={!r}>'.format(
            self.__class__.__name__, self.function_name, self.args, self.kwargs
        )

    @contextmanager
    def set_working_dir(self):
        """ Spooler uses its own working dir, so it's better to change cwd
            to initial project dir, cause some path resolution mechanic
            in Django depends on it, see #3.
        """
        current_dir = os.getcwd()
        working_dir = self.setup.get('working_dir')

        if working_dir and os.path.exists(working_dir):
            os.chdir(working_dir)

        try:
            yield
        finally:
            os.chdir(current_dir)

    def __call__(self, *args, **kwargs):
        if not uwsgi and self.executor != TaskExecutor.RUNTIME:
            return

        self.args = args
        self.kwargs = kwargs

        return self.execute_async()

    def __getstate__(self):
        return {
            'function_name': self.function_name,
            'args': self.args,
            'kwargs': self.kwargs,
            'setup': self.setup,
        }

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._function = None
        self._buffer = None

    @property
    def buffer(self):
        if not self._buffer:
            self._buffer = ProxyDict(self.setup, 'buffer')

        return self._buffer

    @property
    def function(self):
        if self._function is None:
            self._function = load_function(self.function_name)

        return self._function

    def add_setup(self, **kwargs):
        self.setup.update(kwargs)

    def execute_now(self):
        logger.info('Executing %r', self)
        setattr(self.function, self.attr_name, self)
        with self.set_working_dir():
            return self.function(*self.args, **self.kwargs)

    def execute_async(self):
        raise NotImplementedError()

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs)


class RuntimeTask(BaseTask):
    executor = TaskExecutor.RUNTIME

    def execute_now(self):
        serialize(self.args)
        serialize(self.kwargs)
        super(RuntimeTask, self).execute_now()

    def execute_async(self):
        return self.execute_now()


class MuleTask(BaseTask):
    """
        Note: task function may not be initialized - mule be able to
        import it successfully.
    """
    executor = TaskExecutor.MULE
    mule_task_id = 'uwsgi_tasks.mule_task'
    default_mule_id = 0

    def get_message_content(self):
        # TODO: cache?
        return serialize({self.mule_task_id: self})

    @classmethod
    def extract_from_message(cls, message):
        msg = deserialize(message)
        mule_task = msg.get(cls.mule_task_id)

        if mule_task is None:
            return None

        return mule_task

    def execute_async(self):
        mule_id = self.setup.get('mule', self.default_mule_id)
        return uwsgi.mule_msg(self.get_message_content(), mule_id)

    @classmethod
    def create(cls, **kwargs):
        if 'mule' not in uwsgi.opt and 'mules' not in uwsgi.opt:
            return None

        self = cls(**kwargs)

        if len(self.get_message_content()) > UWSGI_MAXIMUM_MESSAGE_SIZE:
            return None

        return self


class SpoolerTask(BaseTask):
    executor = TaskExecutor.SPOOLER
    spooler_default_arguments = (
        'message_dict', 'spooler', 'priority', 'at'
    )

    def __init__(self, function, **setup):
        super(SpoolerTask, self).__init__(function, **setup)
        self.retry_count = self.setup.pop('retry_count', None)
        self.retry_timeout = self.setup.pop('retry_timeout', None)

    def get_message_content(self):
        base_message_dict = self.__getstate__()
        base_message_dict['setup'] = serialize(self.setup)

        for key in self.spooler_default_arguments:
            if key in self.setup:
                base_message_dict[key] = self.setup[key]

        # datetime and timedelta conversion
        at = base_message_dict.get('at')

        if at:
            if isinstance(at, timedelta):
                at += datetime.utcnow()

            if isinstance(at, datetime):
                at = calendar.timegm(at.timetuple())

            base_message_dict['at'] = str(at)

        logger.debug('Spooler base parameters: "%r"', base_message_dict)

        message_dict = base_message_dict.copy()
        message_dict.update({
            'args': serialize(self.args),
            'kwargs': serialize(self.kwargs)
        })

        if len(repr(message_dict)) >= UWSGI_MAXIMUM_MESSAGE_SIZE:
            # message too long for spooler - we have to use `body` parameter
            message_dict = base_message_dict
            message_dict.update({
                'args': serialize(()),
                'kwargs': serialize({}),
                'body': serialize({
                    'args': self.args,
                    'kwargs': self.kwargs,
                })
            })

        return self._encode_message(message_dict)

    @staticmethod
    def _encode_message(message_dict):
        """ Encode message key, since spooler accept only bytes literals """

        def encoder(s):
            try:
                if isinstance(s, six.text_type):
                    return s.encode()
            except UnicodeEncodeError:
                pass
            return s

        return {encoder(k): encoder(v) for k, v in six.iteritems(message_dict)}

    @staticmethod
    def _decode_message(message_dict):

        def decoder(s, decode=True):
            try:
                if decode and isinstance(s, six.binary_type):
                    return s.decode()
            except UnicodeDecodeError:
                pass
            return s

        return {
            decoder(k): decoder(v, k in {b'function_name'})
            for k, v in six.iteritems(message_dict)
        }

    def execute_async(self):
        return uwsgi.spool(self.get_message_content())

    def execute_now(self):
        try:
            result = super(SpoolerTask, self).execute_now()
        except RetryTaskException as ex:
            # let's retry the task
            return self.retry(count=ex.count, timeout=ex.timeout)

        spool_return = self.setup.get('spooler_return')

        if result == SPOOL_RETRY and not spool_return:
            return self.retry()

        if not spool_return:
            return SPOOL_OK

        return result

    @property
    def retry_count(self):
        return self.setup.get('retry_count')

    @retry_count.setter
    def retry_count(self, value):
        if value is None:
            value = 0

        if not isinstance(value, int):
            raise TypeError('retry_count must be integer '
                            'got {}'.format(type(value)))
        self.setup['retry_count'] = value

    @property
    def retry_timeout(self):
        return self.setup.get('retry_timeout')

    @retry_timeout.setter
    def retry_timeout(self, value):
        if not value:
            return

        if isinstance(value, timedelta):
            value = value.seconds
        elif not isinstance(value, int):
            raise TypeError('retry_timeout must be integer or timedelta'
                            ' got {}'.format(type(value)))

        self.setup['retry_timeout'] = value

    @property
    def at(self):
        return self.setup.get('at')

    @at.setter
    def at(self, value):
        self.setup['at'] = value

    def retry(self, count=None, timeout=None):
        retry_count = count or self.retry_count
        retry_timeout = timeout or self.retry_timeout or 0

        if retry_count is not None:
            if retry_count > 1:
                # retry task
                self.retry_timeout = retry_timeout
                self.retry_count = retry_count - 1
                self.at = timedelta(seconds=self.retry_timeout)
                self.execute_async()
            else:
                logger.info('Stop retrying for %r', self)

        return SPOOL_OK

    @classmethod
    def extract_from_message(cls, message):
        message = cls._decode_message(message)
        func_name = message.get('function_name')

        if not func_name:
            return None

        if 'body' in message:
            body = deserialize(message['body'])
            args = body['args']
            kwargs = body['kwargs']
        else:
            args = deserialize(message['args'])
            kwargs = deserialize(message['kwargs'])

        setup = deserialize(message.get('setup'))

        return cls(
            function=func_name,
            args=args,
            kwargs=kwargs,
            **setup
        )

    @classmethod
    def create(cls, **kwargs):
        if 'spooler' not in uwsgi.opt:
            return None

        return cls(**kwargs)


class SignalTask(BaseTask):
    default_target = ''

    def __init__(self, function, **setup):
        super(SignalTask, self).__init__(function, **setup)
        self._signal_id = None

    def get_default_target(self):
        """List of all available targets can be found here:
        http://uwsgi-docs.readthedocs.org/en/latest/PythonModule.html#uwsgi.register_signal
        """
        if not uwsgi:
            return self.default_target

        executor = self.setup.get('executor', TaskExecutor.AUTO)

        if (('mule' in uwsgi.opt or 'mules' in uwsgi.opt)
                and executor in (TaskExecutor.AUTO, TaskExecutor.MULE)):
            return 'mule'

        if 'spooler' in uwsgi.opt and executor in (TaskExecutor.AUTO,
                                                   TaskExecutor.SPOOLER):
            return 'spooler'

        return self.default_target

    @property
    def signal_id(self):
        if self._signal_id is None:
            try:
                self._signal_id = int(self.setup.get('signal'))
            except (ValueError, TypeError):
                self._signal_id = get_free_signal()

        return self._signal_id

    @property
    def target(self):
        target = self.setup.get('target')
        return target if target is not None else self.get_default_target()

    @target.setter
    def target(self, value):
        self.setup['target'] = value

    def register_signal(self, handler=None):
        if uwsgi and self._signal_id is None:
            handler = handler or self.signal_handler
            uwsgi.register_signal(self.signal_id, self.target, handler)
            return True
        return False

    def free_signal(self):
        # currently uwsgi does not support signal deletion
        self._signal_id = None

    def signal_handler(self, signal):
        self.args = (signal,)
        return self.execute_now()

    def execute_async(self):
        if not uwsgi:
            return

        self.register_signal()
        uwsgi.signal(self.signal_id)
        self.free_signal()


class TimerTask(SignalTask):

    def execute_async(self):
        if not uwsgi:
            return

        self.register_signal(self.signal_handler)

        seconds = self.setup.get('seconds', 0)
        iterations = self.setup.get('iterations', None)

        if iterations is None:
            uwsgi.add_timer(self.signal_id, seconds)
        else:
            uwsgi.add_rb_timer(self.signal_id, seconds, iterations)


class CronTask(SignalTask):

    def execute_async(self):
        if not uwsgi:
            return

        self.register_signal(self.signal_handler)

        minute = self.setup.get('minute')
        hour = self.setup.get('hour')
        day = self.setup.get('day')
        month = self.setup.get('month')
        dayweek = self.setup.get('dayweek')

        uwsgi.add_cron(
            self.signal_id,
            minute,
            hour,
            day,
            month,
            dayweek
        )


class OneTimeTask(TimerTask):
    """It's like TimerTask but executes only ones"""
    executor = TaskExecutor.AUTO

    def __init__(self, function, **setup):
        if 'signal' not in setup:
            warnings.warn(
                'You should provide "signal" parameter, otherwise '
                'first found free signal id will be locked unless '
                'worker is restarted',
                RuntimeWarning
            )

        setup.update({
            'seconds': 0,
            'iterations': 1,
        })
        super(OneTimeTask, self).__init__(function, **setup)


tasks_registry = collections.OrderedDict((
    (TaskExecutor.MULE, MuleTask),
    (TaskExecutor.SPOOLER, SpoolerTask),
    (TaskExecutor.RUNTIME, RuntimeTask),
))


class Task(object):
    """Actual Task factory"""

    def __init__(self, func, executor=TaskExecutor.AUTO, **setup):
        assert callable(func)

        self.function = func
        self.function_name = get_function_path(self.function)
        self.executor = executor
        self.setup = setup
        self._add_to_global_storage()

    def __call__(self, *args, **kwargs):

        if not uwsgi:
            logger.warning('UWSGI environment is not available, so task %r '
                           'will be executed at runtime', self)
            self.executor = TaskExecutor.RUNTIME

        # create task and execute it at runtime or send to mule\spooler
        task = self.get_task(args, kwargs)

        logger.info('Executing asynchronously %s', task)
        task.execute_async()
        return task

    def get_task(self, args, kwargs):
        task_arguments = dict(
            function=self.function,
            args=args,
            kwargs=kwargs,
        )
        task_arguments.update(self.setup)

        current_task = None

        if self.executor in tasks_registry:
            task_class = tasks_registry.get(self.executor)
            current_task = task_class.create(**task_arguments)

        if not current_task:
            for task_class in tasks_registry.values():
                current_task = task_class.create(**task_arguments)
                if current_task:
                    break

        if current_task:
            return current_task

        raise RuntimeError(
            'Could not create a task for "{}" and executor "{}"'.format(
                self.function_name, self.executor)
        )

    def _add_to_global_storage(self):
        setattr(saved_tasks, self.function_name, self.function)

    def __repr__(self):
        return '<TaskFactory: "{}">'.format(self.function_name)
