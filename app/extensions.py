"""Flask application extensions."""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from celery import Celery

db = SQLAlchemy()
migrate = Migrate()
celery = Celery()


def init_celery(app, celery_instance=None):
    """Initialize Celery with Flask app context."""
    if celery_instance is None:
        celery_instance = celery
    celery_instance.conf.update(
        broker_url=app.config.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        result_backend=app.config.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
        timezone="Asia/Shanghai",
        enable_utc=True,
        beat_schedule=app.config.get("CELERY_BEAT_SCHEDULE", {}),
    )

    class ContextTask(celery_instance.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_instance.Task = ContextTask
    return celery_instance
