import asyncio

from app.whatsapp import send_text

number = input("Your WhatsApp number (example 8801712345678): ").strip()
asyncio.run(send_text(number, "Hello from Student Money Agent"))
print("Sent. Check WhatsApp.")