"""Swayam command line.

Eight verbs, all local-first:

    swayam chat          interactive conversation loop (persistent)
    swayam ask "..."     one conversational turn (persistent)
    swayam undo          undo the last action (persistent)
    swayam sim [name]    replay a scenario against the mock runtime (in-memory)
    swayam tasks         current state, read from the projection (persistent)
    swayam log           the event log, in append order (persistent)
    swayam today         tasks due today + events today (persistent)
    swayam week          tasks this week + events this week (persistent)
    swayam overdue       overdue tasks (persistent)
    swayam upcoming      upcoming tasks + events (persistent)
    swayam completed     completed tasks (persistent)
    swayam events        all events (persistent)
    swayam clashes       check for overlapping events (persistent)

The event log view is not a debug convenience. Making the log legible to the person who
owns the data is the cheapest available trust mechanism, and it is the first thing to
reach for when an answer looks wrong.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from swayam.core.clock import FrozenClock
from swayam.interfaces.cli.scenarios import SCENARIOS, get_scenario
from swayam.runtime import build_runtime

app = typer.Typer(
    add_completion=False,
    help="Swayam — a conversational interface to your own commitments.",
)

# Pinned clock so `swayam sim` is reproducible run to run. A real session would use
# SystemClock; the runtime takes either.
DEMO_NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)

# Default data directory for persistent storage.
data_dir_env = os.environ.get("SWAYAM_DATA_DIR")
if data_dir_env:
    DEFAULT_DATA_DIR = Path(data_dir_env)
else:
    DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "swayam"
DEFAULT_EVENTS_PATH = DEFAULT_DATA_DIR / "events.jsonl"


def _banner(persistent: bool = True) -> None:
    typer.secho("Swayam", fg=typer.colors.CYAN, bold=True)
    if persistent:
        msg = f"  {datetime.now():%A %d %B %Y} · persistent event log ({DEFAULT_EVENTS_PATH})\n"
        typer.secho(msg, fg=typer.colors.BRIGHT_BLACK)
    else:
        msg = f"  {DEMO_NOW:%A %d %B %Y} · in-memory event log\n"
        typer.secho(msg, fg=typer.colors.BRIGHT_BLACK)


@app.command()
def ask(utterance: Annotated[str, typer.Argument(help="What you want to say.")]) -> None:
    """Run a single conversational turn (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        result = await runtime.orchestrator.handle(utterance)
        _banner()
        typer.echo(f"> {utterance}\n")
        typer.echo(result.reply.text)
        if result.reply.clarification:
            typer.secho(f"  {result.reply.clarification}", fg=typer.colors.YELLOW)
        _footer(result.intent.value, len(result.committed))

    asyncio.run(run())


@app.command()
def undo() -> None:
    """Undo the last action (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        result = await runtime.orchestrator.undo()
        _banner()
        typer.echo(result.reply.text)
        if result.reply.clarification:
            typer.secho(f"  {result.reply.clarification}", fg=typer.colors.YELLOW)
        _footer(result.intent.value, len(result.committed))

    asyncio.run(run())


@app.command()
def chat() -> None:
    """Start an interactive conversation loop (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        msg = "Interactive chat. Type 'exit' or 'quit' to leave.\n"
        typer.secho(msg, fg=typer.colors.BRIGHT_BLACK)
        while True:
            try:
                utterance = typer.prompt("> ")
            except (EOFError, KeyboardInterrupt):
                typer.echo("\nGoodbye!")
                break
            if utterance.strip().lower() in {"exit", "quit"}:
                typer.echo("Goodbye!")
                break
            if utterance.strip().lower() == "undo":
                result = await runtime.orchestrator.undo()
                typer.echo(result.reply.text)
                if result.reply.clarification:
                    typer.secho(f"  {result.reply.clarification}", fg=typer.colors.YELLOW)
                typer.secho(
                    f"  [{result.intent.value} · {len(result.committed)} events]",
                    fg=typer.colors.BRIGHT_BLACK,
                )
                continue
            if not utterance.strip():
                continue
            result = await runtime.orchestrator.handle(utterance)
            typer.echo(result.reply.text)
            if result.reply.clarification:
                typer.secho(f"  {result.reply.clarification}", fg=typer.colors.YELLOW)
            typer.secho(
                f"  [{result.intent.value} · {len(result.committed)} events]",
                fg=typer.colors.BRIGHT_BLACK,
            )

    asyncio.run(run())


@app.command()
def sim(
    name: Annotated[
        str | None,
        typer.Argument(help="Scenario name. Omit to list them."),
    ] = None,
) -> None:
    """Replay a scripted conversation against the mock runtime (in-memory)."""

    if name is None:
        typer.echo("Scenarios:\n")
        for scenario in SCENARIOS:
            gate = f"[{scenario.pending_milestone}]" if scenario.pending_milestone else "[M2]"
            typer.echo(f"  {scenario.name:<22} {gate:<8} {scenario.description}")
        typer.echo("\nRun one with:  swayam sim capture_weekly_tasks")
        raise typer.Exit

    try:
        scenario = get_scenario(name)
    except KeyError:
        typer.secho(f"No scenario named {name!r}.", fg=typer.colors.RED)
        raise typer.Exit(code=1) from None

    async def run() -> None:
        runtime = await build_runtime(clock=FrozenClock(DEMO_NOW))
        _banner(persistent=False)
        typer.secho(f"{scenario.name} — {scenario.description}\n", fg=typer.colors.CYAN)
        if scenario.pending_milestone:
            typer.secho(
                f"  pending {scenario.pending_milestone}: asserts the system declines\n",
                fg=typer.colors.YELLOW,
            )
        for turn in scenario.turns:
            result = await runtime.orchestrator.handle(turn.utterance)
            typer.echo(f"> {turn.utterance}")
            typer.echo(f"  {result.reply.text}")
            if result.reply.clarification:
                typer.secho(f"  {result.reply.clarification}", fg=typer.colors.YELLOW)
            typer.secho(
                f"  [{result.intent.value} · {len(result.committed)} events]",
                fg=typer.colors.BRIGHT_BLACK,
            )
        _summary(runtime, scenario.name)

    asyncio.run(run())


@app.command()
def tasks() -> None:
    """Show current task state, read from the projection (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        open_tasks = runtime.projection.open_tasks()
        if not open_tasks:
            typer.echo("No tasks in the projection.\n")
        else:
            for task in open_tasks:
                due = f"  due {task.due_date}" if task.due_date else "  undated"
                typer.echo(f"  · {task.title}{due}")
            typer.echo("")

    asyncio.run(run())


@app.command("log")
def show_log() -> None:
    """Print the event log in append order (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        page = await runtime.storage.events.read_all()
        if not page.events:
            typer.secho("Event log is empty.", fg=typer.colors.BRIGHT_BLACK)
            return
        for recorded in page.events:
            typer.secho(
                f"  #{recorded.seq} {recorded.stream_type}/{recorded.event.type}",
                fg=typer.colors.CYAN,
            )
            typer.echo(
                json.dumps(
                    {
                        "occurred_at": recorded.event.occurred_at.isoformat(),
                        "actor": recorded.event.actor.type.value,
                        "causation": str(recorded.event.causation_id or ""),
                        "payload": recorded.event.payload,
                    },
                    indent=2,
                )
            )

    asyncio.run(run())


# --- Query commands ---


@app.command()
def today() -> None:
    """Show tasks due today and events happening today (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        today = date.today()

        tasks = runtime.projection.tasks_today(today)
        events = runtime.projection.events_today(today)

        if tasks:
            typer.secho("Tasks due today:", fg=typer.colors.GREEN, bold=True)
            for t in tasks:
                overdue = " (OVERDUE)" if t.is_overdue else ""
                due_time = f" at {t.due_time}" if t.due_time else ""
                typer.echo(f"  · {t.title}{due_time}{overdue}")
            typer.echo("")

        if events:
            typer.secho("Events today:", fg=typer.colors.CYAN, bold=True)
            for e in events:
                loc = f" @ {e.location}" if e.location else ""
                typer.echo(f"  · {e.title} ({e.kind}) {e.starts_at:%H:%M}-{e.ends_at:%H:%M}{loc}")
            typer.echo("")

        if not tasks and not events:
            typer.echo("Nothing scheduled for today.\n")

    asyncio.run(run())


@app.command()
def week() -> None:
    """Show tasks due this week and events this week (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        today = date.today()

        tasks = runtime.projection.tasks_this_week(today)
        events = runtime.projection.events_this_week(today)

        if tasks:
            typer.secho("Tasks this week:", fg=typer.colors.GREEN, bold=True)
            for t in tasks:
                overdue = " (OVERDUE)" if t.is_overdue else ""
                due_time = f" at {t.due_time}" if t.due_time else ""
                typer.echo(f"  · {t.title} — {t.due_date}{due_time}{overdue}")
            typer.echo("")

        if events:
            typer.secho("Events this week:", fg=typer.colors.CYAN, bold=True)
            for e in events:
                loc = f" @ {e.location}" if e.location else ""
                typer.echo(f"  · {e.title} ({e.kind}) {e.starts_at:%a %d %b %H:%M}{loc}")
            typer.echo("")

        if not tasks and not events:
            typer.echo("Nothing scheduled this week.\n")

    asyncio.run(run())


@app.command()
def overdue() -> None:
    """Show overdue tasks (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        today = date.today()

        tasks = runtime.projection.tasks_overdue(today)

        if tasks:
            typer.secho("Overdue tasks:", fg=typer.colors.RED, bold=True)
            for t in tasks:
                due_time = f" at {t.due_time}" if t.due_time else ""
                days_overdue = (today - (t.due_date or today)).days
                day_str = "day" if days_overdue == 1 else "days"
                typer.echo(
                    f"  · {t.title}{due_time} "
                    f"(due {t.due_date}, {days_overdue} {day_str} overdue)"
                )
            typer.echo("")
        else:
            typer.echo("No overdue tasks. Great job!\n")

    asyncio.run(run())


@app.command()
def upcoming(
    days: Annotated[int, typer.Option(help="Days ahead to show (default 30).")] = 30,
) -> None:
    """Show upcoming tasks and events (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        today = date.today()

        tasks = runtime.projection.tasks_upcoming(today, days)
        events = runtime.projection.events_upcoming(today, days)

        if tasks:
            typer.secho(f"Upcoming tasks (next {days} days):", fg=typer.colors.GREEN, bold=True)
            for t in tasks:
                due_time = f" at {t.due_time}" if t.due_time else ""
                typer.echo(f"  · {t.title} — {t.due_date}{due_time}")
            typer.echo("")

        if events:
            typer.secho(f"Upcoming events (next {days} days):", fg=typer.colors.CYAN, bold=True)
            for e in events:
                loc = f" @ {e.location}" if e.location else ""
                typer.echo(f"  · {e.title} ({e.kind}) {e.starts_at:%a %d %b %H:%M}{loc}")
            typer.echo("")

        if not tasks and not events:
            typer.echo(f"Nothing scheduled in the next {days} days.\n")

    asyncio.run(run())


@app.command()
def completed(
    days: Annotated[int, typer.Option(help="Days back to show (default 30).")] = 30,
) -> None:
    """Show recently completed tasks (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()

        tasks = runtime.projection.tasks_completed(days)

        if tasks:
            typer.secho(f"Completed tasks (last {days} days):", fg=typer.colors.GREEN, bold=True)
            for t in tasks:
                typer.echo(f"  ✓ {t.title}")
            typer.echo("")
        else:
            typer.echo(f"No tasks completed in the last {days} days.\n")

    asyncio.run(run())


@app.command()
def events() -> None:
    """Show all events (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        today = date.today()

        all_events = runtime.projection.events_upcoming(today, days=365)

        if all_events:
            typer.secho("All events:", fg=typer.colors.CYAN, bold=True)
            for e in all_events:
                loc = f" @ {e.location}" if e.location else ""
                notes = f" - {e.notes}" if e.notes else ""
                line = (
                    f"  · {e.title} ({e.kind}) "
                    f"{e.starts_at:%a %d %b %H:%M}-{e.ends_at:%H:%M}{loc}{notes}"
                )
                typer.echo(line)
            typer.echo("")
        else:
            typer.echo("No events scheduled.\n")

    asyncio.run(run())


@app.command()
def clashes() -> None:
    """Check for overlapping events (persistent)."""

    async def run() -> None:
        runtime = await build_runtime(path=DEFAULT_EVENTS_PATH)
        _banner()
        today = date.today()

        clashes = runtime.projection.detect_clashes(today)

        if clashes:
            typer.secho("⚠  Clashing events:", fg=typer.colors.RED, bold=True)
            for c in clashes:
                e1, e2 = c.event1, c.event2
                line1 = f"  {e1.title} ({e1.starts_at:%a %d %b %H:%M}-{e1.ends_at:%H:%M}) "
                line2 = (
                    f"overlaps with {e2.title} "
                    f"({e2.starts_at:%a %d %b %H:%M}-{e2.ends_at:%H:%M})"
                )
                typer.secho(
                    line1 + line2,
                    fg=typer.colors.YELLOW,
                )
            typer.echo("")
        else:
            typer.echo("No clashes detected. Schedule looks clean!\n")

    asyncio.run(run())


def _footer(intent: str, committed: int) -> None:
    typer.secho(
        f"\n  {intent} · {committed} events written",
        fg=typer.colors.BRIGHT_BLACK,
    )


def _summary(runtime: object, scenario_name: str) -> None:
    projection = runtime.projection  # type: ignore[attr-defined]
    open_tasks = projection.open_tasks()
    typer.echo("")
    typer.secho(f"  {len(open_tasks)} open task(s) in the projection", fg=typer.colors.GREEN)
    for task in open_tasks:
        due = f"  due {task.due_date}" if task.due_date else "  undated"
        typer.echo(f"    · {task.title}{due}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
