"""AWS Lambda entry point."""

from mangum import Mangum

from .app import app

handler = Mangum(app, lifespan="off")
