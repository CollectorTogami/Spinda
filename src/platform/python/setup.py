from setuptools import setup
import re
import os
import os.path
import sys
import subprocess


def get_version_component(piece):
    env = os.environ.copy()
    if env.get('BINDIR'):
        # version.cmake looks at the working tree location. Point PWD at the
        # build dir when packaging from the local workspace so version lookup is
        # stable across shell and CMake-driven invocations.
        env['PWD'] = env['BINDIR']
    return subprocess.check_output(['cmake', '-DPRINT_STRING={}'.format(piece), '-P', '../../../version.cmake'], env=env).decode('utf-8').strip()


version = '{}.{}.{}'.format(*(get_version_component(p) for p in ('LIB_VERSION_MAJOR', 'LIB_VERSION_MINOR', 'LIB_VERSION_PATCH')))
git_tag = get_version_component('GIT_TAG')
git_rev = get_version_component('GIT_REV')
git_commit_short = get_version_component('GIT_COMMIT_SHORT')
# Older local checkouts can report empty or placeholder git metadata. Only add
# the dev suffix when the revision fields are actually usable.
if not git_tag and git_rev not in ('', '-1') and git_commit_short not in ('', '(unknown)'):
    version += '.{}+g{}'.format(git_rev, git_commit_short)

setup(
    name="mgba",
    version=version,
    author="Jeffrey Pfau",
    author_email="jeffrey@endrift.com",
    url="http://github.com/mgba-emu/mgba/",
    packages=["mgba"],
    setup_requires=['cffi>=1.6', 'pytest-runner'],
    install_requires=['cffi>=1.6', 'cached-property'],
    extras_require={'pil': ['Pillow>=2.3'], 'cinema': ['pytest']},
    tests_require=['pytest'],
    cffi_modules=["_builder.py:ffi"],
    # The local Qt/Python bridge changes frequently while iterating on RNG
    # tooling. Force the CFFI extension to rebuild so new exported entry points
    # actually reach the built package instead of hiding behind a stale cache.
    options={'build_ext': {'force': True}},
    license="MPL 2.0",
    classifiers=[
        "Programming Language :: C",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
        "Topic :: Games/Entertainment",
        "Topic :: System :: Emulators"
    ]
)
