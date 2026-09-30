"""Swayam command line.

Four verbs, all local-first:

    swayam ask "..."     one conversational turn
    swayam sim [name]    replay a scenario against the mock runtime
    swayam tasks         current state, read from the projection
    swayam log           the event log, in append order

The event log view is not a debug convenience. Making the log legible to the person who
owns the data is the cheapest available trust mechanism, and it is the first thing to
reach for when an answer looks wrong.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
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


def _banner() -> None:
    typer.secho("Swayam", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"  {DEMO_NOW:%A %d %B %Y} · in-memory event log\n", fg=typer.colors.BRIGHT_BLACK)


@app.command()
def ask(utterance: Annotated[str, typer.Argument(help="What you want to say.")]) -> None:
    """Run a single conversational turn."""

    async def run() -> None:
        runtime = await build_runtime(clock=FrozenClock(DEMO_NOW))
        result = await runtime.orchestrator.handle(utterance)
        _banner()
        typer.echo(f"> {utterance}\n")
        typer.echo(result.reply.text)
        if result.reply.clarification:
            typer.secho(f"  {result.reply.clarification}", fg=typer.colors.YELLOW)
        _footer(result.intent.value, len(result.committed))

    asyncio.run(run())


@app.command()
def sim(
    name: Annotated[
        str | None,
        typer.Argument(help="Scenario name. Omit to list them."),
    ] = None,
) -> None:
    """Replay a scripted conversation against the mock runtime."""

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
        _banner()
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
    """Show current task state, read from the projection."""

    async def run() -> None:
        runtime = await build_runtime(clock=FrozenClock(DEMO_NOW))
        _banner()
        open_tasks = runtime.projection.open_tasks()
        if not open_tasks:
            typer.echo("No tasks in the projection.\n")
        else:
            for task in open_tasks:
                due = f"  due {task.due_date}" if task.due_date else "  undated"
                typer.echo(f"  · {task.title}{due}")
            typer.echo("")
        # Phase 1 storage is in-memory, so state does not survive between invocations.
        # Saying so is better than letting `swayam tasks` look like it lost the user's data.
        typer.secho(
            "  Storage is in-memory in Phase 1 — state does not persist between\n"
            "  invocations. The Postgres adapter lands with M3.\n",
            fg=typer.colors.BRIGHT_BLACK,
        )
        typer.echo('  Try:  swayam ask "add a task to send the Q3 report due friday"')

    asyncio.run(run())


@app.command("log")
def show_log() -> None:
    """Print the event log in append order."""

    async def run() -> None:
        runtime = await build_runtime(clock=FrozenClock(DEMO_NOW))
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
