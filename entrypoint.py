"""Command-line entrypoint for the job tracker."""

import click

from commands.apply import apply_command
from commands.evaluate import evaluate_command
from commands.generate_criteria import generate_criteria_command
from commands.pipeline import pipeline_command
from commands.profile_review import profile_review_command
from commands.scan import scan_command


@click.group()
def cli():
    """Job tracker command-line tools."""


cli.add_command(profile_review_command)
cli.add_command(generate_criteria_command)
cli.add_command(scan_command)
cli.add_command(pipeline_command)
cli.add_command(evaluate_command)
cli.add_command(apply_command)


if __name__ == "__main__":
    cli()
