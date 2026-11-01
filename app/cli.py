import click
from flask import current_app
from flask.cli import with_appcontext

from .services.errors import ServiceError
from .services.users import company_exists, create_company_with_admin


@click.command("create-company")
@click.option("--company-name", prompt=True)
@click.option("--admin-name", prompt=True)
@click.option("--email", prompt=True)
@click.password_option()
@with_appcontext
def create_company(company_name, admin_name, email, password):
    """Create the company and its first administrator (same as the /setup page)."""
    if company_exists():
        raise click.ClickException("A company already exists. This version supports one company.")
    try:
        create_company_with_admin(company_name=company_name, admin_name=admin_name, email=email, password=password)
    except ServiceError as e:
        raise click.ClickException(str(e))
    click.echo("Company and administrator created.")


@click.command("run-worker")
@click.option("--once", is_flag=True, help="Process everything currently queued, then exit.")
@click.option("--poll", type=float, default=None, help="Seconds to wait when the queue is empty.")
@with_appcontext
def run_worker_cmd(once, poll):
    """Run the document-conversion worker (polls the database queue)."""
    from .services.conversion_queue import run_worker
    click.echo("Conversion worker started. Press Ctrl+C to stop.")
    try:
        n = run_worker(once=once, poll_seconds=poll)
    except KeyboardInterrupt:
        click.echo("Stopped.")
        return
    click.echo(f"Processed {n} conversion(s).")


def register_cli(app):
    app.cli.add_command(create_company)
    app.cli.add_command(run_worker_cmd)
