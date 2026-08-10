"""Pure signal/array code: numpy, scipy, mne only.

Nothing here may import app.config, app.models, sqlalchemy or pydantic, so these
modules stay usable from a notebook or a standalone script. Job orchestration
that needs the DB lives in app/services/.
"""
