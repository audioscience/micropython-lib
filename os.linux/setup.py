import sys
# Remove current dir from sys.path, otherwise setuptools will peek up our
# module instead of system.
sys.path.pop(0)
from setuptools import setup


setup(name='micropython-os.linux',
      version='0.0.1',
      description='os.linux module for MicroPython',
      long_description="Linux specific OS functions not included in micropython-lib's os module",
      author='Delio Brignoli',
      author_email='dbrignoli@audioscience.com',
      maintainer='Delio Brignoli',
      maintainer_email='dbrignoli@audioscience.com',
      license='MIT',
      packages=['os.linux'])
