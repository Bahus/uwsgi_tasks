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
from uwsgi_tasks import task, TaskExecutor, set_uwsgi_callbacks

# set default callbacks for mules and spoolers
set_uwsgi_callbacks()

@task(executor=TaskExecutor.MULE)
def send_email_async(subject, body, email_to):
    return send_mail(subject, body, 'noreply@domain.com', [email_to])
 
...

def my_view():
    # Execute tasks asynchronously on first available mule
    send_email_async('Welcome!', 'Thank you!', 'user@domain.com')
```

Execution of `send_email_async` will not block execution of `my_view`, since 
function will be called by first available mule.

The following tasks execution backends are supported:
* `AUTO` - default mode, mule will be used if it is available and _pickled_ task's arguments less than 64 KB in size, otherwise spooler will be used. If spooler is not available, than task is executed at runtime.
* `MULE` - execute decorated task on first available mule
* `SPOOLER` - execute decorated task on spooler
* `RUNTIME` - execute task at runtime

When `SPOOLER` backend is used, the following additional parameters are supported:
* `priority` - string related to priority of this task, larger = less important, so you can simply use digits. `spooler-ordered` parameter must be set for this feature to work.
* `at` - UNIX timestamp or Python **datetime** or Python **timedelta** object.
* `spooler_return` - boolean value, `False` by default. If `True` is passed, you can return spooler codes from function, e.g. `SPOOL_OK`, `SPOOL_RETRY` and `SPOOL_IGNORE`.
* `retry_count` - how many times spooler should repeat the task if it returns `SPOOL_RETRY` code, implies `spooler_return=True`.
* `retry_timeout` - how many seconds between attempts spooler shoud wait to execute the task. Actual timeout depends on `spooler-frequency` parameter. Python **timedelta** object is also supported.

**Use case**: run task asynchronously and repeat execution 3 times at maximum if it fails, with 5 seconds timeout between attempts.

```python
from functools import wraps
from uwsgi_tasks import task, TaskExecutor


def task_wrapper(func):
    @wraps(func)  # required!
    def _inner(*args, **kwargs):
        print 'Task started with parameters:', args, kwargs
        try:
            func(*args, **kwargs)
        except Exception as ex:  # example
            print 'Exception is occurred', ex, 'repeat the task'
            return uwsgi.SPOOL_RETRY

        print 'Task ended', func
        return uwsgi.SPOOL_OK

    return _inner

@task(executor=TaskExecutor.SPOOLER, retry_count=3, retry_timeout=5)
@task_wrapper
def spooler_task(text):
    print 'Hello, spooler! text =', text
    raise Exception('Sorry, task failed!')
```

#### There are some important notes:

Tasks should be imported on project initialization, thus if you use Django, you may place your tasks in `app/tasks.py` file and import them in `app/__init__.py`:

```python
# app/__init__.py
from .tasks import *
```

If Django's version is >= 1.7, you are encouraged to use `AppConfig.ready` method as described in official [documentation](https://docs.djangoproject.com/en/1.7/ref/applications/#configuring-applications):

```python
from importlib import import_module
from django.apps import AppConfig

class IndexConfig(AppConfig):
    name = 'project.apps.index'
    verbose_name = 'Just index page'

    def ready(self):
        import_module('.tasks', self.name)
```

It's necessary to setup required uwsgi callbacks at project's initialization, you should call `set_uwsgi_callbacks` in your `project/__init__.py` file. Don't do `from uwsgidecorators import *`, otherwise you will override callbacks.

Make sure task's arguments must be [pickable](http://stackoverflow.com/questions/3603581/what-does-it-mean-for-an-object-to-be-picklable-or-pickle-able), since they are serialized and send via socket (mule) or file (spooler).

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
