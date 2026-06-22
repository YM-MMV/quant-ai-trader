"""API route modules (M20).

Each module exposes an ``APIRouter`` that :mod:`apps.api.main` mounts. Routes are
thin: they validate input with Pydantic, delegate to the agent tool layer
(:mod:`apps.agent.tools`) / services, and return typed responses. There is no
live-execution route — the API is paper-only, exactly like the agent.
"""
