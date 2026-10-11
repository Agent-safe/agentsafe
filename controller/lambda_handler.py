"""AWS Lambda entry point for the full FastAPI controller and dashboard."""
from mangum import Mangum

from app import app

handler = Mangum(app, lifespan="on")
