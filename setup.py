import setuptools
import re

with open("README.md", "r") as fh:
    long_description = fh.read()

with open("mirror/__init__.py", "r") as fh:
    match = re.search(r'__version__ = "(.*)"', fh.read())
    if match:
        version = match.group(1)
    else:
        version = "unknown"

with open("requirements.txt", "r") as fh:
    install_requires = fh.read().splitlines()

setuptools.setup(
    name="mirror.py",
    version=version,
    author="Roul",
    author_email="geoul@sparcs.org",
    description="Mirror Manager written in python",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.11.0",
    install_requires=install_requires,
    entry_points={
        "console_scripts": [
            "mirror = mirror.__main__:main",
        ]
    },
)
