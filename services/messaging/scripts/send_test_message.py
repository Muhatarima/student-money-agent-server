import asyncio
import sys
from pathlib import Path

# `python scripts/send_test_message.py` only puts scripts/ on the import path,
# so add the service folder (one level up) to make `import app` work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.whatsapp import send_text  # noqa: E402

number = input("Your WhatsApp number (example 8801712345678): ").strip()
asyncio.run(send_text(number, "Hello from Student Money Agent"))
print("Sent. Check WhatsApp.")
