# -*- coding: utf-8 -*-
from __future__ import print_function
import io
import sys
import subprocess

import os
from setuptools import setup


if sys.argv[-1] == 'test':
    # python-mock is required to run unit-tests
    import unittest
    unittest.main('uwsgi_tasks.tests', argv=sys.argv[:-1])


def get_long_description():
    with io.open('./README.md', encoding='utf-8') as f:
        readme = f.read()
    path = None
    pandoc_paths = ('/usr/local/bin/pandoc', '/usr/bin/pandoc')
    for p in pandoc_paths:
        if os.path.exists(p):
            path = p
            break

    if path is None:
        print('Pandoc not found, tried: {}'.format(pandoc_paths))
        return readme

    cmd = [path, '--from=markdown', '--to=rst']
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    doc = readme.encode('utf8', errors='replace')
    rst = p.communicate(doc)[0]

    if sys.version_info[0] > 2:
        # PY3
        return rst.decode()
    else:
        return rst

setup(
    name='uwsgi-tasks',
    packages=['uwsgi_tasks'],
    version='0.6.3',
    description='Asynchronous tasks management with UWSGI server',
    author='Oleg Churkin',
    author_email='bahusoff@gmail.com',
    url='https://github.com/Bahus/uwsgi_tasks',
    keywords=['asynchronous', 'tasks', 'uwsgi'],
    platforms='Platform Independent',
    license='MIT',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Web Environment',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License'
    ],
    long_description=get_long_description(),
    requires=['uwsgi', 'six'],
    install_requires=['six'],
)
