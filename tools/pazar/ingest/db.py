"""The connection to the shared database and the migrations of the tool (through the shared module)."""
from . import config, pipeline


def connect(dsn=None):
    """Autocommit, as the shared module expects: every write is in an explicit transaction."""
    return pipeline.connect(dsn or config.DSN)


def migrate(c):
    pipeline.require_core(c, "0011")
    return pipeline.migrate(c, config.TOOL, config.ROOT)
