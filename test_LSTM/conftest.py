"""
conftest.py — Pytest setup for test_LSTM/.
 
This file is auto-discovered by pytest. It adds backend/LSTM/ to sys.path
so the tests can `import preprocessing`, `import anomaly`, etc., without
the project needing to be installed as a package.
 
Run the tests from the backend/ folder:
    cd backend
    pytest test_LSTM/ -v
"""
 
import sys
from pathlib import Path
 
# Tests live in backend/test_LSTM/; source lives in backend/LSTM/.
THIS_DIR = Path(__file__).resolve().parent
SRC_DIR = THIS_DIR.parent / "LSTM"
sys.path.insert(0, str(SRC_DIR))
 