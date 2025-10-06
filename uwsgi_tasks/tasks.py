from __future__ import annotations

import calendar
import collections
import inspect
import logging
import os
import pickle
import threading
import traceback
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from uwsgi_tasks.utils import (
    ProxyDict,
    get_function_path,
    import_by_path,
)

# --- uWSGI import and setup ---
try:
    import uwsgi

    if uwsgi.masterpid() == 0:
        warnings.warn('uWSGI master process must be enabled for uwsgi_tasks to work.', RuntimeWarning)
        uwsgi = None
except ImportError:
    uwsgi = None

# --- Spooler constants ---
SPOOL_OK: int = getattr(uwsgi, 'SPOOL_OK', -2)
SPOOL_IGNORE: int = getattr(uwsgi, 'SPOOL_IGNORE', 0)
SPOOL_RETRY: int = getattr(uwsgi, 'SPOOL_RETRY', -1)

# --- Globals ---
UWSGI_MAXIMUM_MESSAGE_SIZE: int = 64 * 1024
saved_tasks = threading.local()
logger = logging.getLogger('uwsgi_tasks')

# --- Type Variables ---
CallableT = TypeVar('CallableT', bound=Callable[..., Any])
BaseTaskT = TypeVar('BaseTaskT', bound='BaseTask')


# --- Helper Functions ---

def serialize(content: Any) -> bytes:
    """Serialize content using pickle."""
    return pickle.dumps(content)


def deserialize(serialized: bytes) -> Any:
    """Deserialize content using pickle."""
    return pickle.loads(serialized)


def load_function(func_name: str) -> Callable[..., Any]:
    """Load a function by its dotted path, with a fallback to a local cache."""
    func = getattr(saved_tasks, func_name, None)
    if func:
        return func
    return import_by_path(func_name)


def manage_spool_request(message: Dict[bytes, bytes]) -> int:
    """uWSGI spooler callback. Deserializes a task and executes it."""
    logger.debug('Processing request for spooler')
    try:
        task = SpoolerTask.extract_from_message(message)
        if task:
            return task.execute_now()
    except Exception:
        traceback.print_exc()
        logger.exception('Spooler message ignored: "%s"', message)
    return SPOOL_OK


def manage_mule_request(message: bytes) -> Any:
    """uWSGI mule message hook. Deserializes a task and executes it."""
    logger.debug('Processing request for mule')
    task = MuleTask.extract_from_message(message)
    if task:
        return task.execute_now()
    return None


def get_free_signal() -> Optional[int]:
    """Finds and returns the number of an available uWSGI signal."""
    if not uwsgi:
        return None
    for signum in range(256):
        if not uwsgi.signal_registered(signum):
            return signum
    raise RuntimeError('No free uWSGI signal available.')


def set_uwsgi_callbacks() -> None:
    """Sets the uWSGI callbacks for the spooler and mule messages."""
    if uwsgi:
        uwsgi.spooler = manage_spool_request
        uwsgi.mule_msg_hook = manage_mule_request


set_uwsgi_callbacks()


def get_current_task() -> Optional[BaseTask]:
    """Introspection API: allows getting the current task instance from within the called function."""
    stack = inspect.stack()
    try:
        frame = stack[1][0]
        caller_name = frame.f_code.co_name
        caller_task_factory = frame.f_globals.get(caller_name)
        if caller_task_factory and hasattr(caller_task_factory, 'function'):
            return getattr(caller_task_factory.function, BaseTask.attr_name, None)
    except (IndexError, KeyError):
        logger.exception('get_current_task failed')
    return None


# --- Exceptions ---

class RetryTaskException(Exception):
    """Throw this exception to re-execute a spooler task."""

    def __init__(self, count: Optional[int] = None, timeout: Optional[int] = None):
        self.count = count
        self.timeout = timeout
        super().__init__(f"Retrying task (count={count}, timeout={timeout})")


# --- Task Executors ---

class TaskExecutor(IntEnum):
    AUTO = 1
    SPOOLER = 2
    MULE = 3
    RUNTIME = 4


# --- Base Task Classes ---

class BaseTask:
    attr_name: str = 'uwsgi_task'
    executor: Optional[TaskExecutor] = None

    def __init__(self, function: Union[Callable[..., Any], str], **setup: Any):
        self._function: Optional[Callable[..., Any]] = None
        if isinstance(function, str):
            self.function_name: str = function
        elif callable(function):
            self._function = function
            self.function_name = get_function_path(function)
        else:
            raise TypeError('A callable or a dotted import path must be provided.')

        self.args: Tuple[Any, ...] = setup.pop('args', ())
        self.kwargs: Dict[str, Any] = setup.pop('kwargs', {})
        self.setup: Dict[str, Any] = setup or {}
        self.setup.setdefault('working_dir', os.getcwd())
        self._buffer: Optional[ProxyDict] = None

    def __repr__(self) -> str:
        return f'<Task: {self.__class__.__name__} for "{self.function_name}" with args={self.args!r}, kwargs={self.kwargs!r}>'

    @contextmanager
    def set_working_dir(self) -> Generator[None, None, None]:
        """A context manager to temporarily change the working directory."""
        working_dir = self.setup.get('working_dir')
        if working_dir and os.path.exists(working_dir):
            current_dir = os.getcwd()
            os.chdir(working_dir)
            try:
                yield
            finally:
                os.chdir(current_dir)
        else:
            yield

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not uwsgi and self.executor != TaskExecutor.RUNTIME:
            return None
        self.args = args
        self.kwargs = kwargs
        return self.execute_async()

    def __getstate__(self) -> Dict[str, Any]:
        return {
            'function_name': self.function_name,
            'args': self.args,
            'kwargs': self.kwargs,
            'setup': self.setup,
        }

    def __setstate__(self, state: Dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._function = None
        self._buffer = None

    @property
    def buffer(self) -> ProxyDict:
        """A dict-like object to pass data between task execution attempts."""
        if self._buffer is None:
            self._buffer = ProxyDict(self.setup, 'buffer')
        return self._buffer

    @property
    def function(self) -> Callable[..., Any]:
        """The actual callable function for this task."""
        if self._function is None:
            self._function = load_function(self.function_name)
        return self._function

    def add_setup(self, **kwargs: Any) -> None:
        """Add or update setup parameters for the task."""
        self.setup.update(kwargs)

    def execute_now(self) -> Any:
        """Executes the task synchronously in the current process."""
        logger.info('Executing %r', self)
        setattr(self.function, self.attr_name, self)
        with self.set_working_dir():
            return self.function(*self.args, **self.kwargs)

    def execute_async(self) -> Any:
        """Executes the task asynchronously."""
        raise NotImplementedError()

    @classmethod
    def create(cls: Type[BaseTaskT], **kwargs: Any) -> Optional[BaseTaskT]:
        """Creates an instance of the task."""
        return cls(**kwargs)


# --- Concrete Task Implementations ---

class RuntimeTask(BaseTask):
    """A task that runs immediately in the same process."""
    executor = TaskExecutor.RUNTIME

    def execute_now(self) -> Any:
        serialize(self.args)
        serialize(self.kwargs)
        return super().execute_now()

    def execute_async(self) -> Any:
        return self.execute_now()


class MuleTask(BaseTask):
    """A task to be executed by a uWSGI mule."""
    executor = TaskExecutor.MULE
    mule_task_id: str = 'uwsgi_tasks.mule_task'
    default_mule_id: int = 0

    def get_message_content(self) -> bytes:
        return serialize({self.mule_task_id: self})

    @classmethod
    def extract_from_message(cls: Type[MuleTask], message: bytes) -> Optional[MuleTask]:
        msg = deserialize(message)
        return msg.get(cls.mule_task_id)

    def execute_async(self) -> Optional[int]:
        if not uwsgi:
            return None
        mule_id = self.setup.get('mule', self.default_mule_id)
        return uwsgi.mule_msg(self.get_message_content(), mule_id)

    @classmethod
    def create(cls: Type[MuleTask], **kwargs: Any) -> Optional[MuleTask]:
        if not uwsgi or ('mule' not in uwsgi.opt and 'mules' not in uwsgi.opt):
            return None

        instance = cls(**kwargs)
        if len(instance.get_message_content()) > UWSGI_MAXIMUM_MESSAGE_SIZE:
            logger.warning("Task %r is too large for a mule message.", instance)
            return None
        return instance


class SpoolerTask(BaseTask):
    """A task to be executed by a uWSGI spooler."""
    executor = TaskExecutor.SPOOLER
    spooler_default_arguments: Tuple[str, ...] = ('message_dict', 'spooler', 'priority', 'at')

    def __init__(self, function: Union[Callable[..., Any], str], **setup: Any):
        super().__init__(function, **setup)
        self.retry_count = self.setup.pop('retry_count', None)
        self.retry_timeout = self.setup.pop('retry_timeout', None)

    def get_message_content(self) -> Dict[bytes, bytes]:
        base_message_dict = self.__getstate__()
        base_message_dict['setup'] = serialize(self.setup)

        for key in self.spooler_default_arguments:
            if key in self.setup:
                base_message_dict[key] = self.setup[key]

        at = base_message_dict.get('at')
        if isinstance(at, timedelta):
            at = datetime.utcnow() + at
        if isinstance(at, datetime):
            base_message_dict['at'] = str(calendar.timegm(at.timetuple()))

        logger.debug('Spooler base parameters: "%r"', base_message_dict)

        message_dict = base_message_dict.copy()
        message_dict.update({'args': serialize(self.args), 'kwargs': serialize(self.kwargs)})

        if len(repr(message_dict)) >= UWSGI_MAXIMUM_MESSAGE_SIZE:
            message_dict = base_message_dict
            message_dict.update({
                'args': serialize(()),
                'kwargs': serialize({}),
                'body': serialize({'args': self.args, 'kwargs': self.kwargs}),
            })

        return self._encode_message(message_dict)

    @staticmethod
    def _encode_message(message_dict: Dict[str, Any]) -> Dict[bytes, bytes]:
        encoded = {}
        for k, v in message_dict.items():
            key_bytes = str(k).encode()
            if isinstance(v, str):
                value_bytes = v.encode()
            elif isinstance(v, bytes):
                value_bytes = v
            else:
                value_bytes = str(v).encode()  # Fallback for other types like int
            encoded[key_bytes] = value_bytes
        return encoded

    @staticmethod
    def _decode_message(message_dict: Dict[bytes, bytes]) -> Dict[str, Union[str, bytes]]:
        decoded = {}
        for k, v in message_dict.items():
            key_str = k.decode()
            if key_str == 'function_name':
                decoded[key_str] = v.decode()
            else:
                decoded[key_str] = v
        return decoded

    def execute_async(self) -> Optional[int]:
        if not uwsgi:
            return None
        return uwsgi.spool(self.get_message_content())

    def execute_now(self) -> int:
        try:
            result = super().execute_now()
        except RetryTaskException as e:
            return self.retry(count=e.count, timeout=e.timeout)

        spooler_return = self.setup.get('spooler_return', False)
        if not spooler_return:
            return SPOOL_RETRY if result == SPOOL_RETRY else SPOOL_OK
        return result

    @property
    def retry_count(self) -> int:
        return self.setup.get('retry_count', 0)

    @retry_count.setter
    def retry_count(self, value: Optional[int]):
        self.setup['retry_count'] = int(value or 0)

    @property
    def retry_timeout(self) -> Optional[int]:
        return self.setup.get('retry_timeout')

    @retry_timeout.setter
    def retry_timeout(self, value: Optional[Union[int, timedelta]]):
        if isinstance(value, timedelta):
            self.setup['retry_timeout'] = value.seconds
        elif value is not None:
            self.setup['retry_timeout'] = int(value)

    @property
    def at(self) -> Optional[Union[int, datetime, timedelta]]:
        return self.setup.get('at')

    @at.setter
    def at(self, value: Optional[Union[int, datetime, timedelta]]):
        self.setup['at'] = value

    def retry(self, count: Optional[int] = None, timeout: Optional[int] = None) -> int:
        retry_count = count if count is not None else self.retry_count
        if retry_count > 1:
            self.retry_timeout = timeout if timeout is not None else self.retry_timeout
            self.retry_count = retry_count - 1
            self.at = timedelta(seconds=self.retry_timeout or 0)
            self.execute_async()
        else:
            logger.info('Stop retrying for %r', self)
        return SPOOL_OK

    @classmethod
    def extract_from_message(cls: Type[SpoolerTask], message: Dict[bytes, bytes]) -> Optional[SpoolerTask]:
        decoded_message = cls._decode_message(message)
        func_name = decoded_message.get('function_name')
        if not func_name:
            return None

        if 'body' in decoded_message:
            body = deserialize(decoded_message['body'])
            args, kwargs = body['args'], body['kwargs']
        else:
            args = deserialize(decoded_message['args'])
            kwargs = deserialize(decoded_message['kwargs'])

        setup = deserialize(decoded_message['setup'])

        return cls(function=func_name, args=args, kwargs=kwargs, **setup)

    @classmethod
    def create(cls: Type[SpoolerTask], **kwargs: Any) -> Optional[SpoolerTask]:
        return cls(**kwargs) if uwsgi and 'spooler' in uwsgi.opt else None


class SignalTask(BaseTask):
    """A task triggered by a uWSGI signal."""
    default_target: str = ''

    def __init__(self, function: Union[Callable[..., Any], str], **setup: Any):
        super().__init__(function, **setup)
        self._signal_id: Optional[int] = None

    def get_default_target(self) -> str:
        if not uwsgi:
            return self.default_target
        executor = self.setup.get('executor', TaskExecutor.AUTO)
        if ('mule' in uwsgi.opt or 'mules' in uwsgi.opt) and executor in (TaskExecutor.AUTO, TaskExecutor.MULE):
            return 'mule'
        if 'spooler' in uwsgi.opt and executor in (TaskExecutor.AUTO, TaskExecutor.SPOOLER):
            return 'spooler'
        return self.default_target

    @property
    def signal_id(self) -> Optional[int]:
        if self._signal_id is None:
            self._signal_id = self.setup.get('signal') or get_free_signal()
        return self._signal_id

    @property
    def target(self) -> str:
        return self.setup.get('target') or self.get_default_target()

    @target.setter
    def target(self, value: str):
        self.setup['target'] = value

    def register_signal(self, handler: Optional[Callable[[int], Any]] = None) -> bool:
        if uwsgi and self.signal_id is not None and not uwsgi.signal_registered(self.signal_id):
            uwsgi.register_signal(self.signal_id, self.target, handler or self.signal_handler)
            return True
        return False

    def free_signal(self) -> None:
        # uWSGI does not support unregistering signals.
        self._signal_id = None

    def signal_handler(self, signal: int) -> Any:
        self.args = (signal,)
        return self.execute_now()

    def execute_async(self) -> None:
        if not uwsgi or self.signal_id is None:
            return
        self.register_signal()
        uwsgi.signal(self.signal_id)
        self.free_signal()


class TimerTask(SignalTask):
    """A task executed periodically by a uWSGI timer."""

    def execute_async(self) -> None:
        if not uwsgi or self.signal_id is None:
            return
        self.register_signal(self.signal_handler)
        seconds = self.setup.get('seconds', 0)
        iterations = self.setup.get('iterations')
        if iterations is None:
            uwsgi.add_timer(self.signal_id, seconds)
        else:
            uwsgi.add_rb_timer(self.signal_id, seconds, iterations)


class CronTask(SignalTask):
    """A task executed on a cron-like schedule."""

    def execute_async(self) -> None:
        if not uwsgi or self.signal_id is None:
            return
        self.register_signal(self.signal_handler)
        uwsgi.add_cron(
            self.signal_id,
            self.setup.get('minute', -1),
            self.setup.get('hour', -1),
            self.setup.get('day', -1),
            self.setup.get('month', -1),
            self.setup.get('dayweek', -1),
        )


# --- Task Factory ---

tasks_registry: Dict[int, Type[BaseTask]] = collections.OrderedDict([
    (TaskExecutor.MULE, MuleTask),
    (TaskExecutor.SPOOLER, SpoolerTask),
    (TaskExecutor.RUNTIME, RuntimeTask),
])


class Task:
    """A factory for creating and executing tasks."""

    def __init__(self, func: Callable[..., Any], executor: TaskExecutor = TaskExecutor.AUTO, **setup: Any):
        if not callable(func):
            raise TypeError("A callable function must be provided.")
        self.function = func
        self.function_name = get_function_path(self.function)
        self.executor = executor
        self.setup = setup
        self._add_to_global_storage()

    def __call__(self, *args: Any, **kwargs: Any) -> Optional[BaseTask]:
        if not uwsgi:
            logger.warning('uWSGI not available. Task %r will run at runtime.', self)
            self.executor = TaskExecutor.RUNTIME

        task = self.get_task(args, kwargs)
        if task:
            logger.info('Executing asynchronously %s', task)
            task.execute_async()
        return task

    def get_task(self, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Optional[BaseTask]:
        task_arguments = {
            'function': self.function,
            'args': args,
            'kwargs': kwargs,
            **self.setup,
        }

        # If executor is specified and not AUTO, try it first.
        if self.executor != TaskExecutor.AUTO:
            task_class = tasks_registry.get(self.executor)
            if task_class:
                task = task_class.create(**task_arguments)
                if task:
                    return task

        # Fallback for AUTO or if the specified executor failed.
        for task_class in tasks_registry.values():
            task = task_class.create(**task_arguments)
            if task:
                return task

        logger.error('Could not create any task for "%s"', self.function_name)
        return None

    def _add_to_global_storage(self) -> None:
        setattr(saved_tasks, self.function_name, self.function)

    def __repr__(self) -> str:
        return f'<TaskFactory: "{self.function_name}">'