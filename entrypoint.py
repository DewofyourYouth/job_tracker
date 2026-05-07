"""Command-line entrypoint for the job tracker."""

import click

from commands.profile_review import profile_review_command


@click.group()
def cli():
    """Job tracker command-line tools."""


cli.add_command(profile_review_command)


if __name__ == "__main__":
    cli()
