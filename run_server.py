import sys
import os

# Ensure the project root is in PYTHONPATH
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

from uvicorn import run
from app.main import app

if __name__ == "__main__":
    run(app, host="0.0.0.0", port=8000, reload=True)
