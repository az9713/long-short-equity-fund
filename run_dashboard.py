import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    app = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.port", "8502",
    ])
