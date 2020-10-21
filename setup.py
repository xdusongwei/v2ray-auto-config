from setuptools import setup, find_packages

setup(
    name='v2ray-auto-config',
    version='0.2.11',
    author='songwei',
    author_email='songwei@songwei.io',
    url='https://github.com/xdusongwei/v2ray-auto-config',
    description='',
    long_description='',
    zip_safe=False,
    packages=find_packages(),
    install_requires=['aiohttp[speedups]', 'aiohttp_socks', ],
    python_requires='>=3.7.0',
)
