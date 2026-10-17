"""One command to rebuild everything and open the dashboard.

    python run.py              # generate data -> map codes -> analyze -> launch the app
    python run.py --no-app     # rebuild the data only

AFP mapping reuses cached Claude answers, so this costs nothing and needs no API key
unless a description has never been mapped before.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = [
    ("Generating synthetic statements", ["-m", "src.generate_data"]),
    ("Mapping fee descriptions to AFP codes", ["-m", "src.map_codes"]),
    ("Running the analysis", ["-m", "src.analyze"]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-app", action="store_true", help="rebuild the data but do not launch the app")
    args = parser.parse_args()
    for label, cmd in STEPS:
        print(f"\n== {label} ==", flush=True)
        subprocess.run([sys.executable, *cmd], cwd=ROOT, check=True)
    if not args.no_app:
        print("\n== Launching dashboard (Ctrl+C to stop) ==", flush=True)
        subprocess.run([sys.executable, "-m", "streamlit", "run", "app/streamlit_app.py"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
