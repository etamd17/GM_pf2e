PR4A transactional-store migrations
===================================

Run migrations explicitly with `python -m alembic upgrade head`. Application
startup deliberately does not import this environment or run migrations.
