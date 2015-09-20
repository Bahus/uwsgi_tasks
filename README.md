# UWSGI Tasks engine

This package makes it to use [UWSGI signal framework](http://uwsgi-docs.readthedocs.org/en/latest/Signals.html)
for asynchronous tasks management. It's more functional and flexible than [cron scheduler](https://wikipedia.org/wiki/Cron), and
can be used as replacement for [celery](http://www.celeryproject.org/) in many cases.

## Requirements

The module works only in [UWSGI web server](https://uwsgi-docs.readthedocs.org/en/latest/) environment,
you also may have to setup some [mules](https://uwsgi-docs.readthedocs.org/en/latest/Mules.html) or\and [spooler processes](http://uwsgi-docs.readthedocs.org/en/latest/Spooler.html) as described in UWSGI documentation.

## Installation

Simple execute `pip install uwsgi_tasks`

## Usage

### Mules, farms and spoolers

**Use case**: you have Django project and want to send all emails asynchronously.

Setup some mules with `--mule` or `--mules=<N>` parameters, or some spooler
processes with `--spooler==<path_to_spooler_folder>`.

Then write:

```python
# myapp/__init__.py
from django.core.mail import send_mail
from uwsgi_tasks import task, TaskExecutor

@task(executor=TaskExecutor.SPOOLER)
def send_email_async(subject, body, email_to):
    # Execute task asynchronously on first available spooler
    return send_mail(subject, body, 'noreply@domain.com', [email_to])

...

def my_view():
    # Execute tasks asynchronously on first available spooler
    send_email_async('Welcome!', 'Thank you!', 'user@domain.com')
```

Execution of `send_email_async` will not block execution of `my_view`, since
function will be called by first available spooler. I personally recommend to use spoolers rather than mules for several reasons:

1. Task will be executed\retried even if uwsgi is crashed or restarted, since task information stored in files.
2. Task parameters size is not limited to 64 KBytes.
3. You may switch to external\network spoolers if required.
4. You are able to tune task execution flow with introspection facilities.


The following tasks execution backends are supported:

* `AUTO` - default mode, spooler will be used if available, otherwise mule will be used. If mule is not available, than task is executed at runtime.
* `MULE` - execute decorated task on first available mule
* `SPOOLER` - execute decorated task on spooler
* `RUNTIME` - execute task at runtime, this backend is also used in case `uwsgi` module can't be imported, e.g. tests.

Common task parameters are:

* `working_dir` - absolute path to execute task in. You won't typically need to provide this value, since it will be provided automatically: as soon as you execute the task current working directory will be saved and sent to spooler or mule. You may pass `None` value to disable this feature.

When `SPOOLER` backend is used, the following additional parameters are supported:

* `priority` - **string** related to priority of this task, larger = less important, so you can simply use digits. `spooler-ordered` uwsgi parameter must be set for this feature to work (in linux only?).
* `at` - UNIX timestamp or Python **datetime** or Python **timedelta** object – when task must be executed.
* `spooler_return` - boolean value, `False` by default. If `True` is passed, you can return spooler codes from function, e.g. `SPOOL_OK`, `SPOOL_RETRY` and `SPOOL_IGNORE`.
* `retry_count` - how many times spooler should repeat the task if it returns `SPOOL_RETRY` code, implies `spooler_return=True`.
* `retry_timeout` - how many seconds between attempts spooler should wait to execute the task. Actual timeout depends on `spooler-frequency` parameter. Python **timedelta** object is also supported.

**Use case**: run task asynchronously and repeat execution 3 times at maximum if it fails, with 5 seconds timeout between attempts.

```python
from functools import wraps
from uwsgi_tasks import task, TaskExecutor, SPOOL_OK, SPOOL_RETRY

def task_wrapper(func):
    @wraps(func)  # required!
    def _inner(*args, **kwargs):
        print 'Task started with parameters:', args, kwargs
        try:
            func(*args, **kwargs)
        except Exception as ex:  # example
            print 'Exception is occurred', ex, 'repeat the task'
            return SPOOL_RETRY

        print 'Task ended', func
        return SPOOL_OK

    return _inner

@task(executor=TaskExecutor.SPOOLER, retry_count=3, retry_timeout=5)
@task_wrapper
def spooler_task(text):
    print 'Hello, spooler! text =', text
    raise Exception('Sorry, task failed!')
```

Raising `RetryTaskException(count=<retry_count>, timeout=<retry_timeout>)` approach can be also used to retry task execution:

```python
import logging
from uwsgi_tasks import RetryTaskException, task, TaskExecutor

@task(executor=TaskExecutor.SPOOLER, retry_count=2)
def process_purchase(order_id):

    try:
        # make something with order id
        ...
    except Exception as ex:
        logging.exception('Something bad happened')
        # retry task in 10 seconds for the last time
        raise RetryTaskException(timeout=10)
```

Be careful when providing `count` parameter to the exception constructor - it may lead to infinite tasks execution, since the parameter replaces the value of `retry_count`.

Task execution process can be also controlled via spooler options, see details [here](http://uwsgi-docs.readthedocs.org/en/latest/Spooler.html?highlight=spool_ok#options).

### Project setup

There are some requirements to make asynchronous tasks work properly. Let's imagine your Django project has the following directory structure:

```
├── project/
│   ├── venv/  <-- your virtual environment is placed here
│   ├── my_project/  <-- Django project (created with "startproject" command)
│   │   ├── apps/
│   │   │   ├── index/  <-- Single Django application ("startapp" command)
│   │   │   │   ├── __init__.py
│   │   │   │   ├── admin.py
│   │   │   │   ├── models.py
│   │   │   │   ├── tasks.py
│   │   │   │   ├── tests.py
│   │   │   │   ├── views.py
│   │   │   ├── __init__.py
│   │   ├── __init__.py
│   │   ├── settings.py
│   │   ├── urls.py
│   ├── spooler/  <-- spooler files are created here
```

Minimum working UWSGI configuration is placed in `uwsgi.ini` file:

```ini
[uwsgi]
http-socket=127.0.0.1:8080
processes=1
workers=1

# python path setup
module=django.core.wsgi:get_wsgi_application()
# absolute path to the virtualenv directory
venv=<base_path>/project/venv/
# Django project directory is placed here:
pythonpath=<base_path>/project/
# "importable" path for Django settings
env=DJANGO_SETTINGS_MODULE=my_project.settings

# spooler setup
spooler=<base_path>/project/spooler
spooler-processes=2
spooler-frequency=10
```

In such configuration you should put the following code into `my_project/__init__.py` file:

```python
# my_project/__init__.py
from uwsgi_tasks import set_uwsgi_callbacks

set_uwsgi_callbacks()
```

Task functions (decorated with `@task`) may be placed in any file where they can be imported, e.g. `apps/index/tasks.py`.

If you still receive some strange errors when running asynchronous tasks, e. g.
"uwsgi unable to find the spooler function" or "ImproperlyConfigured Django exception", you may try
the following: add to uwsgi configuration new variable `spooler-import=my_project` - it will force spooler
to import `my_project/__init__.py` file when starting, then add Django initialization
into this file:

```python
# my_project/__init__.py
# ... set_uwsgi_callbacks code ...

# if you use Django, otherwise use
# initialization related to your framework\project
from uwsgi_tasks import django_setup

django_setup()
```

Also make sure you **didn't override** uwsgi callbacks with this code
`from uwsgidecorators import *` somewhere in your project.

If nothing helps - please submit an issue.

If you want to run some cron or timer-like tasks on project initialization you
may import them in the same file:

```python
# my_project/__init__.py
# ... set_uwsgi_callbacks

from my_cron_tasks import *
from my_timer_tasks import *
```

Keep in mind that task arguments must be [pickable](http://stackoverflow.com/questions/3603581/what-does-it-mean-for-an-object-to-be-picklable-or-pickle-able), since they are serialized and send via socket (mule) or file (spooler).

### Timers, red-black timers and cron

This API is similar to uwsgi bundled Python decorators [module](http://uwsgi-docs.readthedocs.org/en/latest/PythonDecorators.html). One thing to note: you are not able to provide any arguments to timer-like or cron-like tasks. See examples below:

```python
from uwsgi_tasks import *

@timer(seconds=5)
def print_every_5_seconds(signal_number):
    """Prints string every 5 seconds

    Keep in mind: task is created on initialization.
    """
    print 'Task for signal', signal_number

@timer(seconds=5, iterations=3, target='workers')
def print_every_5_seconds(signal_number):
    """Prints string every 5 seconds 3 times"""
    print 'Task with iterations for signal', signal_number

@timer_lazy(seconds=5)
def print_every_5_seconds_after_call(signal_number):
    """Prints string every 5 seconds"""
    print 'Lazy task for signal', signal_number

@cron(minute=-2)
def print_every_2_minutes(signal_number):
    print 'Cron task:', signal_number

@cron_lazy(minute=-2, target='mule')
def print_every_2_minutes_after_call(signal_number):
    print 'Cron task:', signal_number

...

def my_view():
   print_every_5_seconds_after_call()
   print_every_2_minutes_after_call()
```

Timer and cron decorators supports `target` parameter, supported values are described [here](http://uwsgi-docs.readthedocs.org/en/latest/PythonModule.html#uwsgi.register_signal).

Keep in mind the maximum number of timer-like and cron-like tasks is 256 for each available worker.

### Task introspection API

Using task introspection API you can get current task object inside current task function and will be able to change some task parameters. You may also use special `buffer` dict-like object to pass data between task execution attempts. Using `get_current_task` you are able to get internal representation of task object and manipulate the attributes of the task, e.g. SpoolerTask object has the following changeable properties: `at`, `retry_count`, `retry_timeout`.

Here is a complex example:

```python
from uwsgi_tasks import get_current_task

@task(executor=TaskExecutor.SPOOLER, at=datetime.timedelta(seconds=10))
def remove_files_sequentially(previous_selected_file=None):
    # get current SpoolerTask object
    current_task = get_current_task()

    selected_file = select_file_for_removal(previous_selected_file)

    # we should stop the task here
    if selected_file is None:
        logger.info('All files were removed')
        for filename, removed_at in current_task.buffer['results'].items():
            logger.info('File "%s" was removed at "%s"', filename, removed_at)
        for filename, error_message in current_task.buffer['errors'].items():
            logger.info('File "%s", error: "%s"', filename, error_message)
        return

    try:
        logger.info('Removing the file "%s"', selected_file)
        # ... remove the file ...
        del_file(selected_file)
    except IOError as ex:
        logger.exception('Cannot delete file "%s"', selected_file)

        # let's try to remove this one more time later
        io_errors = current_task.buffer.setdefault('errors', {}).get(selected_file)
        if not io_errors:
            current_task.buffer['errors'][selected_file] = str(ex)
            current_task.at = datetime.timedelta(seconds=20)
            return current_task(previous_selected_file)

    # save datetime of removal
    current_task.buffer.setdefault('results', {})[selected_file] = datetime.datetime.now()

    # run in async mode
    return current_task(selected_file)
```

#### Changing task configuration before execution

You may use `add_setup` method to change some task-related settings before (or during) task execution process. The following example shows how to change timer's timeout and iterations amount at runtime:

```python
from uwsgi_tasks import timer_lazy

@timer_lazy(target='worker')
def run_me_periodically(signal):
    print('Running with signal:', signal)

def my_view(request):
    run_me_periodically.add_setup(seconds=10, iterations=2)
    run_me_periodically()
```
