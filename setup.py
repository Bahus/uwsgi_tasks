# -*- coding: utf-8 -*-
import sys
from distutils.core import setup


if sys.argv[-1] == 'test':
    # python-mock is required to run unit-tests
    import unittest
    unittest.main('uwsgi_tasks.tests', argv=sys.argv[:-1])


def get_long_description():
    with open('./README.md') as f:
        readme = f.read()

    try:
        import pandoc
    except ImportError:
        return readme

    pandoc.core.PANDOC_PATH = '/usr/local/bin/pandoc'
    doc = pandoc.Document()
    doc.markdown = readme
    return doc.rst


setup(
    name='uwsgi-tasks',
    packages=['uwsgi_tasks'],
    version='0.2',
    description='Asynchronous tasks management with UWSGI server',
    author='Oleg Churkin',
    author_email='bahusoff@gmail.com',
    url='https://github.com/Bahus/uwsgi_tasks',
    keywords=['asynchronous', 'tasks', 'uwsgi'],
    platforms='Platform Independent',
    license='MIT',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License'
    ],
    long_description=get_long_description(),
    requires=['uwsgi'],
)