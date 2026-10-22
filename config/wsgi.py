"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()


def _apply_render_migrations():
    """Apply schema changes before serving, regardless of Render's start command.

    Existing Render services can keep dashboard commands instead of adopting
    render.yaml as a Blueprint. Running migrations from WSGI startup guarantees
    those services do not expose new code against an old database schema.
    A PostgreSQL advisory lock serializes startup when Gunicorn starts multiple
    workers at once.
    """
    if os.environ.get('RENDER', '').lower() != 'true':
        return

    from django.core.management import call_command
    from django.db import connection

    lock_id = 0x524F59414C45  # stable lock key: "ROYAL"
    locked = False
    try:
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_lock(%s)', [lock_id])
            locked = True
        call_command('migrate', interactive=False, verbosity=1)
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [lock_id])


_apply_render_migrations()
