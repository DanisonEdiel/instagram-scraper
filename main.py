import sys
from pathlib import Path

# Permite ejecutar sin instalar el paquete (añadiendo src al PYTHONPATH)
sys.path.append(str(Path(__file__).parent / "src"))

from instagram_scraper.cli import main


if __name__ == "__main__":
    main()