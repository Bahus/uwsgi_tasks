# -*- coding: utf-8 -*-
import sys
from distutils.core import setup


if sys.argv[-1] == 'test':
    import unittest
    unittest.main('uwsgi_tasks.tests', argv=sys.argv[:-1])


setup(
    name='uwsgi-tasks',
    packages=['uwsgi_tasks'],
    version='0.1',
    description='',
    author='Oleg Churkin',
    author_email='bahusoff@gmail.com',
    url='TODO',
    keywords=[],
    platforms='Platform Independent',
    license='',
    classifiers='',
    long_description='',
    requires=['uwsgi'],
)