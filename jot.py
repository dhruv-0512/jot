"""
jot.py — Main CLI entry-point for the `jot` clipboard history manager.

Commands:
  jot ls              Show last 20 clipboard entries (text and images)
  jot get <n>         Copy entry #n back to clipboard
  jot search <query>  Filter history by text
  jot clear           Wipe all history and image files
  jot daemon start    Start background watcher
  jot daemon stop     Stop background watcher
  jot daemon status   Report watcher status
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows so Rich unicode symbols render correctly
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import click
import pyperclip
from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text

import storage
import daemon as _daemon

console = Console()

# ── Constants ──────────────────────────────────────────────────────────────────

_PREVIEW_LEN  = 52   # max chars for text preview
_IMGCAT_WIDTH = 40   # columns for imgcat inline previews

# ── PIL (optional, for image dimensions and clipboard write) ───────────────────

try:
    from PIL import Image  # type: ignore
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ── Helpers ────────────────────────────────────────────────────────────────────


def _truncate(text: str, max_len: int = _PREVIEW_LEN) -> str:
    nl_char = "<LF>" if sys.platform == "win32" else "↵"
    ellipsis_char = "..." if sys.platform == "win32" else "…"
    preview = text.replace("\r\n", f"{nl_char} ").replace("\n", f"{nl_char} ").replace("\r", f"{nl_char} ")
    if len(preview) > max_len:
        preview = preview[:max_len] + ellipsis_char
    return preview


def _fmt_time(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        day = local.day
        return local.strftime(f"%b {day} %H:%M")
    except Exception:
        return iso_utc


def _imgcat_available() -> bool:
    """Return True if imgcat is in PATH (iTerm2 or similar)."""
    return shutil.which("imgcat") is not None


def _terminal_supports_imgcat() -> bool:
    """Heuristic: check common env vars set by imgcat-capable terminals."""
    term_prog = os.environ.get("TERM_PROGRAM", "")
    colorterm  = os.environ.get("COLORTERM", "")
    return (
        "iterm" in term_prog.lower()
        or "wezterm" in term_prog.lower()
        or "kitty" in term_prog.lower()
        or colorterm.lower() in ("truecolor", "24bit")
    ) and _imgcat_available()


def _image_dimensions(path: str) -> str:
    """Return 'WxH' string, or '' on failure."""
    if not _PIL_AVAILABLE:
        return ""
    try:
        with Image.open(path) as img:
            return f"{img.width}×{img.height}"
    except Exception:
        return ""


def _copy_image_to_clipboard(path: str) -> None:
    """Copy a PNG file's raw image data to the system clipboard."""
    if sys.platform == "darwin":
        # macOS: osascript can set clipboard to PNG data
        subprocess.run(
            ["osascript", "-e", f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif sys.platform == "win32":
        # Windows: PowerShell + System.Windows.Forms
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            f'$img = [System.Drawing.Image]::FromFile("{path}"); '
            "[System.Windows.Forms.Clipboard]::SetImage($img); "
            "$img.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # Linux: xclip
        with open(path, "rb") as f:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png"],
                stdin=f,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


# ── Table builder ──────────────────────────────────────────────────────────────


def _build_table(rows: list, title: str | None = None) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        title=title,
        title_style="bold white",
        expand=False,
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("#",       style="dim",   justify="right",  no_wrap=True, min_width=3)
    table.add_column("Type",    style="dim",   justify="center", no_wrap=True, min_width=5)
    table.add_column("Content",                min_width=20, max_width=_PREVIEW_LEN + 8, no_wrap=True)
    table.add_column("Copied",  style="dim",   justify="right",  no_wrap=True, min_width=12)

    for rank, row in enumerate(rows, start=1):
        ctype = row["content_type"]
        ts    = _fmt_time(row["copied_at"])

        if ctype == "image":
            path   = row["content"]
            exists = Path(path).exists()

            if exists:
                dims    = _image_dimensions(path)
                fname   = Path(path).name
                preview = Text()
                preview.append("[img] ", style="bold cyan")
                preview.append(fname, style="cyan")
                if dims:
                    preview.append(f"  {dims}", style="dim")
            else:
                preview = Text("[image expired]", style="dim red")

            table.add_row(str(rank), "img", preview, ts)
        else:
            content = row["content"]
            # Replace newlines with visible marker, avoid non-ASCII on Windows
            preview = _truncate(content)
            table.add_row(str(rank), "txt", preview, ts)

    return table


# ── CLI root ───────────────────────────────────────────────────────────────────

_VERSION = "1.0.0"

_INFO = {
    "name":    "jot",
    "version": _VERSION,
    "desc":    "A fast terminal clipboard history manager with image support",
    "author":  "Dhruv",
    "email":   "dhruvh3vedi@gmail.com",
    "github":  "https://github.com/dhruv-0512",
    "repo":    "https://github.com/dhruv-0512/jot",
    "license": "MIT",
}


def _show_info(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    from rich.panel import Panel
    from rich.text import Text as RichText

    info = RichText()
    info.append("jot", style="bold cyan")
    info.append(f"  v{_INFO['version']}\n", style="dim")
    info.append(f"{_INFO['desc']}\n\n", style="")
    info.append("Author   ", style="bold")
    info.append(f"{_INFO['author']}\n")
    info.append("Email    ", style="bold")
    info.append(f"{_INFO['email']}\n")
    info.append("GitHub   ", style="bold")
    info.append(f"{_INFO['github']}\n")
    info.append("Repo     ", style="bold")
    info.append(f"{_INFO['repo']}\n")
    info.append("License  ", style="bold")
    info.append(f"{_INFO['license']}")

    console.print(Panel(info, border_style="cyan", padding=(1, 2)))
    ctx.exit()


@click.group()
@click.version_option(_VERSION, prog_name="jot")
@click.option("--info", is_flag=True, is_eager=True, expose_value=False,
              callback=_show_info, help="Show project and author info.")
def cli() -> None:
    """jot — a fast clipboard history manager."""


# ── jot ls ────────────────────────────────────────────────────────────────────


@cli.command("ls")
@click.option("-n", "--count", default=20, show_default=True, metavar="N",
              help="Number of entries to show.")
@click.option("--no-preview", is_flag=True, default=False,
              help="Skip inline image previews even if imgcat is available.")
def cmd_ls(count: int, no_preview: bool) -> None:
    """List the last N clipboard entries (text and images)."""
    rows = storage.get_recent(limit=count)

    if not rows:
        console.print("[yellow]No clipboard history yet.[/yellow]")
        console.print(
            "[dim]Start the daemon with [bold]jot daemon start[/bold] to begin recording.[/dim]"
        )
        return

    # Print imgcat previews above the table for any image entries
    use_imgcat = _terminal_supports_imgcat() and not no_preview
    if use_imgcat:
        for rank, row in enumerate(rows, start=1):
            if row["content_type"] == "image" and Path(row["content"]).exists():
                console.print(f"[dim]── #{rank} ──[/dim]")
                try:
                    subprocess.run(
                        ["imgcat", "--width", str(_IMGCAT_WIDTH), row["content"]],
                        check=False,
                    )
                except Exception:
                    pass

    total = storage.total_count()
    table = _build_table(rows)
    console.print(table)

    t_count, i_count = storage.count_by_type()
    console.print(
        f"[dim]Showing {len(rows)} of {total} entries "
        f"({t_count} text, {i_count} image)  •  db: ~/.jot/history.db[/dim]"
    )


# ── jot get <n> ───────────────────────────────────────────────────────────────


@cli.command("get")
@click.argument("n", type=int)
def cmd_get(n: int) -> None:
    """Copy entry #N back to the clipboard."""
    row = storage.get_by_rank(n)
    if row is None:
        console.print(f"[red]No entry at position #{n}.[/red]")
        sys.exit(1)

    ctype = row["content_type"]

    if ctype == "image":
        path = row["content"]
        if not Path(path).exists():
            console.print(f"[red]Image file no longer exists:[/red] {path}")
            sys.exit(1)
        try:
            _copy_image_to_clipboard(path)
        except Exception as exc:
            console.print(f"[red]Failed to copy image to clipboard: {exc}[/red]")
            sys.exit(1)
        console.print(
            f"[green]✓[/green] Copied image [bold]#{n}[/bold] to clipboard "
            f"[dim]({Path(path).name})[/dim]"
        )
    else:
        content = row["content"]
        try:
            pyperclip.copy(content)
        except Exception as exc:
            console.print(f"[red]Failed to copy to clipboard: {exc}[/red]")
            sys.exit(1)
        preview = _truncate(content, max_len=60)
        console.print(
            f"[green]✓[/green] Copied [bold]#{n}[/bold] to clipboard: [dim]{preview}[/dim]"
        )


# ── jot search ────────────────────────────────────────────────────────────────


@cli.command("search")
@click.argument("query")
@click.option("-n", "--count", default=50, show_default=True, metavar="N",
              help="Maximum results to return.")
def cmd_search(query: str, count: int) -> None:
    """Search text clipboard history for QUERY (case-insensitive)."""
    rows = storage.search_entries(query, limit=count)

    if not rows:
        console.print(f"[yellow]No entries matching [bold]{query!r}[/bold].[/yellow]")
        return

    table = _build_table(rows, title=f'Search: "{query}"')
    console.print(table)
    console.print(f"[dim]{len(rows)} result(s)[/dim]")


# ── jot clear ────────────────────────────────────────────────────────────────


@cli.command("clear")
@click.option("-y", "--yes", is_flag=True, default=False,
              help="Skip confirmation prompt.")
def cmd_clear(yes: bool) -> None:
    """Wipe all clipboard history and delete saved image files."""
    total = storage.total_count()

    if total == 0:
        console.print("[yellow]History is already empty.[/yellow]")
        return

    t_count, i_count = storage.count_by_type()
    summary = f"{t_count} text"
    if i_count:
        summary += f", {i_count} image"
        summary += f" (files in {storage.IMG_DIR})"

    if not yes:
        click.confirm(
            f"Delete all {total} entries ({summary}) from jot history?",
            abort=True,
        )

    t_del, i_del = storage.clear_all()
    console.print(
        f"[green]✓[/green] Cleared "
        f"[bold]{t_del}[/bold] text and [bold]{i_del}[/bold] image entries."
    )


# ── jot daemon ────────────────────────────────────────────────────────────────


@cli.group("daemon")
def cmd_daemon() -> None:
    """Manage the background clipboard-watcher daemon."""


@cmd_daemon.command("start")
def daemon_start() -> None:
    """Start the background watcher."""
    ok, msg = _daemon.start()
    style, icon = ("green", "✓") if ok else ("yellow", "!")
    console.print(f"[{style}]{icon}[/{style}] {msg}")
    if not ok:
        sys.exit(1)


@cmd_daemon.command("stop")
def daemon_stop() -> None:
    """Stop the background watcher."""
    ok, msg = _daemon.stop()
    style, icon = ("green", "✓") if ok else ("yellow", "!")
    console.print(f"[{style}]{icon}[/{style}] {msg}")
    if not ok:
        sys.exit(1)


@cmd_daemon.command("status")
def daemon_status() -> None:
    """Report whether the background watcher is running."""
    running, msg = _daemon.status()
    if running:
        console.print(f"[green][ON][/green]  {msg}")
    else:
        console.print(f"[red][OFF][/red] {msg}")


# ── Entry-point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
