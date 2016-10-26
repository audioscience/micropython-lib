import sys
# Remove current dir from sys.path, otherwise setuptools will peek up our
# module instead of system.
sys.path.pop(0)
from setuptools import setup


setup(name='micropython-ubus',
      version='0.0.1',
      description='ubus interface for MicroPython',
      long_description="",
      author='Delio Brignoli',
      author_email='brignoli.delio@gmail.com',
      maintainer='Delio Brignoli',
      maintainer_email='brignoli.delio@gmail.com',
      license='MIT',
      packages=['ubus'])
