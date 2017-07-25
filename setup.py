import platform
from setuptools import setup, find_packages
import pkg_resources

setup(
    name='admin_clipper',
    version='0.1.0',
    description='Clip administrative boundaries at coastlines.',
    long_description='',
    author='Oliver Tonnhofer',
    author_email='olt@omniscale.de',
    url='https://github.com/omniscale/admin_clipper',
    license='Apache Software License 2.0',
    py_modules=['admin_clipper'],
    include_package_data=True,
    entry_points = {
        'console_scripts': [
            'admin-clipper = admin_clipper:main',
        ],
    },
    install_requires=[
        'Shapely',
        'Fiona',
        'pyproj',
    ],
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 2.7",
        "Topic :: Scientific/Engineering :: GIS",
    ],
)
