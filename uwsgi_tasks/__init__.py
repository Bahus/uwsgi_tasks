# -*- coding: utf-8 -*-
from uwsgi_tasks.tasks import (
    Task, SignalTask, TimerTask, CronTask, TaskExecutor, set_uwsgi_callbacks,
    RetryTaskException, SPOOL_OK, SPOOL_RETRY, SPOOL_IGNORE
)
from uwsgi_tasks.decorators import *
