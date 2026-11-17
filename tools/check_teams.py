"""Read three Teams samples, printing only status and counts, never names or pixels."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scriba.app.teams import Reader  # noqa: E402

if __name__ == "__main__":
    reader = Reader()
    try:
        for _ in range(3):
            reading = reader.read()
            print(f"state={reading.state}, people={reading.people}, active={len(reading.active)}, "
                  f"capture={reader.capture_source}, covered={reader.covered}", flush=True)
            time.sleep(0.5)
    finally:
        reader.close()
