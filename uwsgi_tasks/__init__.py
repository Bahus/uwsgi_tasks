# -*- coding: utf-8 -*-
from uwsgi_tasks.tasks import (
    Task, SignalTask, TimerTask, CronTask, TaskExecutor, set_uwsgi_callbacks
)
from uwsgi_tasks.decorators import *
