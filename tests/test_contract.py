import ast
from pathlib import Path

def test_python_parses():
    for path in Path('app').glob('*.py'):
        ast.parse(path.read_text())

def test_review_is_not_automatically_approved():
    worker = Path('app/worker.py').read_text()
    assert "'awaiting_review'" in worker
    assert "'published'" not in worker
