#!/usr/bin/env python3
"""setup.py for cli-anything-drawio.

Install with: pip install -e .
"""

from setuptools import setup, find_namespace_packages

with open("cli_anything/drawio/README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="cli-anything-drawio",
    version="1.0.0",
    author="cli-anything contributors",
    description=(
        "CLI harness for draw.io - create, edit and export diagrams. "
        "Requires: draw.io desktop app (draw.io --export)"
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "defusedxml>=0.7.1",
    ],
    extras_require={"dev": ["pytest>=7.0.0"]},
    entry_points={
        "console_scripts": [
            "cli-anything-drawio=cli_anything.drawio.drawio_cli:main",
        ],
    },
    package_data={
        "cli_anything.drawio": ["skills/*.md", "README.md"],
    },
    include_package_data=True,
    zip_safe=False,
)
