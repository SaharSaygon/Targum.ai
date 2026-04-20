from dotenv import load_dotenv
import anthropic

load_dotenv()

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=256,
    messages=[
        {"role": "user", "content": "Translate 'שלום עולם' to English. Respond with only the translation."}
    ]
)

print(response.content[0].text)
