# jot 📋

A lightweight clipboard history manager for the terminal. Tracks everything you copy — text and images — so you never lose something you copied 10 minutes ago.

## Features

- Saves clipboard history automatically in the background
- Supports both text and images
- Images auto-expire from `/tmp/jot/` on reboot — no clutter
- Deduplicates entries by content hash
- Simple, fast commands you'll actually use daily

## ⚠️ Privacy Notice

jot stores your full clipboard history including any sensitive data (passwords, tokens, auth codes). The database at `~/.jot/history.db` is unencrypted. Don't use on shared machines without understanding this.

## Installation

## Installation

**Via pip:**
```bash
pip install jot-clipboard
```

**From source:**
```bash
git clone https://github.com/yourusername/jot.git
cd jot
pip install -e .
```
## Usage

```bash
jot ls                  # show last 20 clipboard entries
jot ls --n 50           # show last 50 entries
jot get <n>             # copy entry #n back to clipboard
jot search <query>      # search text history
jot clear               # wipe all history
```

### Daemon

```bash
jot daemon start        # start background clipboard watcher
jot daemon stop         # stop the watcher
jot daemon status       # check if running
```

## How it works

`jot daemon start` forks a background process that polls your clipboard every 500ms. New entries are saved to a local SQLite database at `~/.jot/history.db`.

- **Text** entries are stored directly in the DB, capped at 500
- **Image** entries are saved as PNGs to `/tmp/jot/`, path stored in DB, capped at 50. If an image has expired, `jot ls` shows `[image expired]`

## Project Structure

```
jot/
├── jot.py        # CLI entrypoint and all commands
├── daemon.py     # background clipboard watcher
├── storage.py    # SQLite wrapper
└── setup.py      # package config
```

## Requirements

- Python 3.8+
- `click`
- `rich`
- `pyperclip`
- `Pillow`

## License

MIT