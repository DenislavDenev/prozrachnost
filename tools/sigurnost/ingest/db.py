from . import config, pipeline


def connect():
    """A connection to the shared database in autocommit mode; the times are read in UTC."""
    c = pipeline.connect(config.DSN)
    c.execute("SET TIME ZONE 'UTC'")
    return c
