# -*- coding: utf-8 -*-
import collections
import warnings
import logging
import threading
import traceback
import calendar
from datetime import datetime, timedelta

from uwsgi_tasks.utils import (
    import_by_path,
    get_function_path,
)


try:
    import cPickle as pickle
except ImportError:
    import pickle


try:
    import uwsgi
    if uwsgi.masterpid() == 0:
        warnings.warn('Uwsgi master process must be enabled', RuntimeWarning)
        uwsgi = None
except ImportError:
    uwsgi = None


# maximum message size for mule and spooler (64Kb)
# see uwsgi settings `mule-msg-size`
UWSGI_MAXIMUM_MESSAGE_SIZE = 64 * 1024


# storage for saved tasks
# often it's not required since tasks will be imported by path
saved_tasks = threading.local()

# logger
logger = logging.getLogger('uwsgi_tasks')


def load_function(func_name):
    func = getattr(saved_tasks, func_name, None)
    if func:
        return func
    return import_by_path(func_name)


def manage_spool_request(message):
    try:
        task = SpoolerTask.extract_from_message(message)
        if task:
            return task.execute_now()
    except:
        traceback.print_exc()
        logger.exception('Spooler message ignored: "%s"', message)
        return uwsgi.SPOOL_OK


def manage_mule_request(message):
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


class TaskExecutor:
    AUTO = 1
    SPOOLER = 2
    MULE = 3
    RUNTIME = 4


class BaseTask(object):
    executor = None

    def __init__(self, function, **setup):
        self._function = None

        if isinstance(function, basestring):
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

    def __str__(self):
        return u'<Task: {} for "{}">'.format(self.__class__.__name__,
                                             self.function_name)

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
            '_function': None,
            'setup': self.setup,
        }

    def __setstate__(self, state):
        self.__dict__.update(state)

    @property
    def function(self):
        if self._function is None:
            self._function = load_function(self.function_name)

        return self._function

    def execute_now(self):
        return self.function(*self.args, **self.kwargs)

    def execute_async(self):
        raise NotImplementedError()

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs)


class RuntimeTask(BaseTask):
    executor = TaskExecutor.RUNTIME

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
        return pickle.dumps({self.mule_task_id: self})

    @classmethod
    def extract_from_message(cls, message):
        msg = pickle.loads(message)
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

    def get_message_content(self):
        base_message_dict = self.__getstate__()
        base_message_dict['setup'] = pickle.dumps(self.setup)

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

            base_message_dict['at'] = at

        logger.debug('Spooler base parameters: "%r"', base_message_dict)

        message_dict = base_message_dict.copy()
        message_dict.update({
            'args': pickle.dumps(self.args),
            'kwargs': pickle.dumps(self.kwargs)
        })

        if len(repr(message_dict)) >= UWSGI_MAXIMUM_MESSAGE_SIZE:
            # message too long for spooler - we have to use `body` parameter
            message_dict = base_message_dict
            message_dict['body'] = pickle.dumps({
                'args': self.args,
                'kwargs': self.kwargs,
            })

        # TODO: encode utf-8 for python 3 compatibility
        return message_dict

    def execute_async(self):
        return uwsgi.spool(self.get_message_content())

    def execute_now(self):
        result = super(SpoolerTask, self).execute_now()
        spool_return = self.setup.get('spooler_return')

        if result == uwsgi.SPOOL_RETRY and not spool_return:
            return self.retry()

        if not spool_return:
            return uwsgi.SPOOL_OK

        return result

    def retry(self):
        retry_count = self.setup.get('retry_count')
        retry_timeout = self.setup.get('retry_timeout', 0)

        if isinstance(retry_timeout, int):
            retry_timeout = timedelta(seconds=retry_timeout)

        if retry_count and retry_count > 1:
            # retry task
            self.setup['retry_count'] = retry_count - 1
            self.setup['at'] = retry_timeout
            self.execute_async()

        return uwsgi.SPOOL_OK

    @classmethod
    def extract_from_message(cls, message):
        func_name = message.get('function_name')

        if not func_name:
            return None

        if 'body' in message:
            body = pickle.loads(message['body'])
            args = body['args']
            kwargs = body['kwargs']
        else:
            args = pickle.loads(message['args'])
            kwargs = pickle.loads(message['kwargs'])

        setup = pickle.loads(message.get('setup'))

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

    def __init__(self, function, args=None, kwargs=None, **setup):
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
        super(OneTimeTask, self).__init__(function, args, kwargs, **setup)


tasks_registry = collections.OrderedDict((
    (TaskExecutor.MULE, MuleTask),
    (TaskExecutor.SPOOLER, SpoolerTask),
    (TaskExecutor.RUNTIME, RuntimeTask),
))


class Task(object):
    """Actual Task factory"""

    def __init__(self, func, executor=TaskExecutor.AUTO, **setup):
        assert callable(func)

        self.func = func
        self.func_name = get_function_path(self.func)
        self.executor = executor
        self.setup = setup
        self._add_to_global_storage()

    def __call__(self, *args, **kwargs):
        if not uwsgi:
            return False

        # create task and execute it at runtime or send to mule\spooler
        task = self.get_task(args, kwargs)

        logger.info('Executing %s', task)
        task.execute_async()
        return task

    def get_task(self, args, kwargs):
        task_arguments = dict(
            function=self.func,
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
                self.func_name, self.executor)
        )

    def _add_to_global_storage(self):
        setattr(saved_tasks, self.func_name, self.func)
