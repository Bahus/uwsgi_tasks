from __future__ import annotations

from typing import Callable, Any, TypeVar, Optional

from .tasks import Task, TaskExecutor, TimerTask, CronTask, SignalTask

CallableT = TypeVar('CallableT', bound=Callable[..., Any])


def task(
    func: Optional[CallableT] = None,
    executor: int = TaskExecutor.AUTO,
    **setup: Any,
) -> Callable[[CallableT], Task] | Task:
    """A decorator to create a task that can be executed asynchronously."""

    def create_task(function: CallableT) -> Task:
        return Task(function, executor, **setup)

    if func is None:
        return create_task
    return create_task(func)


def timer(
    func: Optional[CallableT] = None,
    seconds: int = 0,
    iterations: Optional[int] = None,
    executor: int = TaskExecutor.AUTO,
    **setup: Any,
) -> Callable[[CallableT], TimerTask] | TimerTask:
    """A decorator to create a timer task that runs on initialization."""
    return timer_lazy(
        func=func,
        seconds=seconds,
        iterations=iterations,
        executor=executor,
        run=True,
        **setup,
    )


def timer_lazy(
    func: Optional[CallableT] = None,
    seconds: int = 0,
    iterations: Optional[int] = None,
    executor: int = TaskExecutor.AUTO,
    run: bool = False,
    **setup: Any,
) -> Callable[[CallableT], TimerTask] | TimerTask:
    """A decorator to create a timer task that can be executed on demand."""

    def inner(function: CallableT) -> TimerTask:
        timer_task = TimerTask(
            function=function,
            executor=executor,
            seconds=seconds,
            iterations=iterations,
            **setup,
        )

        if run:
            return timer_task()

        timer_task.register_signal()
        return timer_task

    if func is None:
        return inner
    return inner(func)


def rbtimer(
    func: Optional[CallableT] = None,
    seconds: int = 0,
    iterations: int = 1,
    executor: int = TaskExecutor.AUTO,
    **setup: Any,
) -> Callable[[CallableT], TimerTask] | TimerTask:
    """A decorator to create a red-black timer task that runs on initialization."""
    return rbtimer_lazy(
        func=func,
        seconds=seconds,
        iterations=iterations,
        executor=executor,
        run=True,
        **setup,
    )


def rbtimer_lazy(
    func: Optional[CallableT] = None,
    seconds: int = 0,
    iterations: int = 1,
    executor: int = TaskExecutor.AUTO,
    run: bool = False,
    **setup: Any,
) -> Callable[[CallableT], TimerTask] | TimerTask:
    """A decorator to create a red-black timer task that can be executed on demand."""
    return timer_lazy(
        func=func,
        seconds=seconds,
        iterations=iterations,
        executor=executor,
        run=run,
        **setup,
    )


def cron(
    func: Optional[CallableT] = None,
    minute: int = -1,
    hour: int = -1,
    day: int = -1,
    month: int = -1,
    dayweek: int = -1,
    executor: int = TaskExecutor.AUTO,
    **setup: Any,
) -> Callable[[CallableT], CronTask] | CronTask:
    """A decorator to create a cron-like task that runs on initialization."""
    return cron_lazy(
        func=func,
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        dayweek=dayweek,
        executor=executor,
        run=True,
        **setup,
    )


def cron_lazy(
    func: Optional[CallableT] = None,
    minute: int = -1,
    hour: int = -1,
    day: int = -1,
    month: int = -1,
    dayweek: int = -1,
    executor: int = TaskExecutor.AUTO,
    run: bool = False,
    **setup: Any,
) -> Callable[[CallableT], CronTask] | CronTask:
    """A decorator to create a cron-like task that can be executed on demand."""

    def inner(function: CallableT) -> CronTask:
        cron_task = CronTask(
            function=function,
            executor=executor,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            dayweek=dayweek,
            **setup,
        )

        if run:
            return cron_task()
        return cron_task

    if func is None:
        return inner
    return inner(func)


def signal(
    func: Optional[CallableT] = None,
    executor: int = TaskExecutor.AUTO,
    **setup: Any,
) -> Callable[[CallableT], SignalTask] | SignalTask:
    """A decorator to create a signal task that runs on initialization."""
    return signal_lazy(
        func=func,
        executor=executor,
        run=True,
        **setup,
    )


def signal_lazy(
    func: Optional[CallableT] = None,
    executor: int = TaskExecutor.AUTO,
    run: bool = False,
    **setup: Any,
) -> Callable[[CallableT], SignalTask] | SignalTask:
    """A decorator to create a signal task that can be executed on demand."""

    def inner(function: CallableT) -> SignalTask:
        signal_task = SignalTask(
            function=function,
            executor=executor,
            **setup,
        )

        if run:
            return signal_task()

        signal_task.register_signal()
        return signal_task

    if func is None:
        return inner
    return inner(func)