#!/usr/bin/env python
from setuptools import setup
import re
import os
import io
try:
    from configparser import ConfigParser
except ImportError:
    from ConfigParser import ConfigParser


def read(fname):
    return io.open(
        os.path.join(os.path.dirname(__file__), fname),
        'r', encoding='utf-8').read()


def get_require_version(name):
    if minor_version % 2:
        require = '%s >= %s.%s.dev0, < %s.%s'
    else:
        require = '%s >= %s.%s, < %s.%s'
    require %= (name, major_version, minor_version,
        major_version, minor_version + 1)
    return require

config = ConfigParser()
config.readfp(open('tryton.cfg'))
info = dict(config.items('tryton'))
for key in ('depends', 'extras_depend', 'xml'):
    if key in info:
        info[key] = info[key].strip().splitlines()
version = info.get('version', '0.0.1')
major_version, minor_version, _ = version.split('.', 2)
major_version = int(major_version)
minor_version = int(minor_version)
name = 'trytond_purchase_requisition'

requires = []
for dep in info.get('depends', []):
    if not re.match(r'(ir|res)(\W|$)', dep):
        requires.append(get_require_version('trytond_%s' % dep))
requires.append(get_require_version('trytond'))

tests_require = [get_require_version('proteus')]
dependency_links = []
if minor_version % 2:
    # Add development index for testing with proteus
    dependency_links.append('https://trydevpi.tryton.org/')

setup(name=name,
    version=version,
    description=('Allows users to enter requests for product supply '
        '(requisition). This will create a "Purchase request" by line '
        'which will be treated by the purchasing department.'),
    long_description=read('README'),
    author='Tryton',
    author_email='maxime.richez@saluc.com',
    url='http://www.tryton.org',
    keywords='tryton purchase requisition',
    package_dir={'trytond.modules.purchase_requisition': '.'},
    packages=[
        'trytond.modules.purchase_requisition',
        'trytond.modules.purchase_requisition.tests',
        ],
    package_data={
        'trytond.modules.purchase_requisition': (info.get('xml', [])
            + ['tryton.cfg', 'view/*.xml', 'locale/*.po', '*.fodt',
                'icons/*.svg', 'tests/*.rst']),
        },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Plugins',
        'Framework :: Tryton',
        'Intended Audience :: Developers',
        'Intended Audience :: Financial and Insurance Industry',
        'Intended Audience :: Legal Industry',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Natural Language :: Bulgarian',
        'Natural Language :: Catalan',
        'Natural Language :: Czech',
        'Natural Language :: Dutch',
        'Natural Language :: English',
        'Natural Language :: French',
        'Natural Language :: German',
        'Natural Language :: Hungarian',
        'Natural Language :: Italian',
        'Natural Language :: Polish',
        'Natural Language :: Portuguese (Brazilian)',
        'Natural Language :: Russian',
        'Natural Language :: Slovenian',
        'Natural Language :: Spanish',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Topic :: Office/Business',
        ],
    license='GPL-3',
    install_requires=requires,
    dependency_links=dependency_links,
    zip_safe=False,
    entry_points="""
    [trytond.modules]
    purchase_requisition = trytond.modules.purchase_requisition
    """,
    test_suite='tests',
    test_loader='trytond.test_loader:Loader',
    tests_require=tests_require,
    use_2to3=True,
    convert_2to3_doctests=[
        'tests/scenario_purchase_requisition.rst',
        ],
    )