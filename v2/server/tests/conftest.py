import sys
import os

# Ensure the server root is on sys.path so `import app.*` resolves
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
