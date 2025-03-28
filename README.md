# What is Klugscheisser?

Klugscheisser is an AI tool designed to make **you** the smartest person in any room—no matter how clueless you actually are.

**Why?**
- ChatGPT and Copilot makes you sound smart behind a screen, but what about in real life?
- Klugscheisser is the answer!

**Two amazing modes:**

- **Q&A Mode:** Answers every question you (pretend to) understand.
- **Translator Mode:** Breaks language barriers, allowing you to misunderstand people internationally.

See [presentation](presentation/presentation.pdf) for more details.

# How to Use

1. Clone the repository:

2. Create .env file in the root directory and add the following:

```bash
DEEPGRAM_API_KEY="<your-deepgram-api-key>"
OPENAI_API_KEY="<your-openai-api-key>"
```

3. Run the server:

```bash
uv run python ./klugscheiser/server.py --http-port 8000 --ws-port 8765
```

You can also run the server with SSL:

```bash
uv run python ./klugscheiser/server.py --http-port 8000 --ws-port 8765 --ssl-cert=/path/to/cert.crt --ssl-key=/path/to/key.key
```

I could recomment to use Tailscale to deploy it on you local network and access it from anywhere with a secure connection.

4. Go to `http://localhost:8000` in your browser. Choose locahost in advanced settings if you are running the server locally.

5. Be the smartest person in the room!


