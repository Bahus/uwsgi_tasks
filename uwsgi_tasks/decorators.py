# -*- coding: utf-8 -*-
from uwsgi_tasks.tasks import Task, TaskExecutor, TimerTask, CronTask

__all__ = ('task', 'timer', 'timer_lazy', 'cron', 'cron_lazy')


def task(func=None, executor=TaskExecutor.AUTO, **setup):

    def create_task(function):
        return Task(function, executor, **setup)

    if callable(func):
        return create_task(func)
    else:
        return lambda f: create_task(f)


def timer(func=None, seconds=0, iterations=None,
          executor=TaskExecutor.AUTO, **setup):
    """Create timer on initialization"""
    return timer_lazy(
        func=func,
        seconds=seconds,
        iterations=iterations,
        executor=executor,
        run=True,
        **setup
    )


def timer_lazy(func=None, seconds=0, iterations=None,
               executor=TaskExecutor.AUTO, run=False, **setup):
    """Create task on execution"""

    def inner(function):
        timer_task = TimerTask(
            function=function,
            executor=executor,
            seconds=seconds,
            iterations=iterations,
            **setup
        )

        if run:
            return timer_task()
        else:
            timer_task.register_signal()
            return timer_task

    if func is None:
        return inner

    return inner(func)


def cron(func=None, minute=-1, hour=-1, day=-1, month=-1, dayweek=-1,
         executor=TaskExecutor.AUTO, **setup):
    """Creates cron-like task on initialization"""
    return cron_lazy(
        func=func,
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        dayweek=dayweek,
        executor=executor,
        run=True,
        **setup
    )


def cron_lazy(func=None, minute=-1, hour=-1, day=-1, month=-1, dayweek=-1,
              executor=TaskExecutor.AUTO, run=False, **setup):
    """Creates cron-like task on execution"""

    def inner(function):
        cron_task = CronTask(
            function=function,
            executor=executor,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            dayweek=dayweek,
            **setup
        )

        if run:
            return cron_task()
        else:
            return cron_task

    if func is None:
        return inner

    return inner(func)
