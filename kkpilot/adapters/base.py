from pathlib import Path
from typing import Protocol

class Extractor(Protocol):
    def extract(self, image_path: Path, context: dict) -> dict: ...
