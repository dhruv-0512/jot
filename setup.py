"""
setup.py — Installable package definition for the `jot` clipboard history manager.

Install with:
    pip install -e .

After installation, the `jot` command is available globally.
"""

from setuptools import setup

setup(
    name="jot-down",
    version="1.0.0",
    description="A fast terminal clipboard history manager with image support",
    long_description=open("README.md", encoding="utf-8").read()
    if __import__("pathlib").Path("README.md").exists()
    else "",
    long_description_content_type="text/markdown",
    author="Dhruv",
    author_email="dhruvh3vedi@gmail.com",
    url="https://github.com/dhruv-0512/jot",
    project_urls={
        "Source": "https://github.com/dhruv-0512/jot",
        "Issues": "https://github.com/dhruv-0512/jot/issues",
    },
    license="MIT",
    python_requires=">=3.10",
    py_modules=["jot", "storage", "daemon"],
    install_requires=[
        "click>=8.1",
        "rich>=13.0",
        "pyperclip>=1.8",
        "Pillow>=10.0",
    ],
    entry_points={
        "console_scripts": [
            "jot=jot:cli",
        ],
    },
    classifiers=[
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Utilities",
    ],
)
