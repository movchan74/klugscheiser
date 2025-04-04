#!/usr/bin/env python
"""
Server for processing audio streams via Deepgram and OpenAI.
It accepts audio chunks over a WebSocket, transcribes them using Deepgram,
and returns processed results (a translation or an answer) to the client.
Configuration (task and language) is provided in the connection URL path.
"""

import argparse
import asyncio
import json
import logging
import os
import ssl  # Added for https support
import time
from typing import Dict, Optional, Tuple

import websockets
from aiohttp import web
from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Initialize OpenAI client.
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = "gpt-4o"


def answer_question(context: str, question: str) -> str:
    """
    Generate a short, concise answer for the given question using the provided context.
    """
    logging.info("Context: %s", context)
    response = openai_client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant providing concise answers. "
                    "Keep your answers very short. For example, if the question is "
                    "'What is the capital of the United States?', answer 'Washington, D.C.'. "
                    "Use the context to understand the question. "
                    "If you cannot answer (e.g., the question is too broad), respond with 'I don't know' or 'Beats me'."
                ),
            },
            {"role": "user", "content": f"Context: {context}\nQuestion: {question}"},
        ],
        max_tokens=150,
    )
    return response.choices[0].message.content.strip()


def is_question(text: str) -> bool:
    """
    Determine if the provided text appears to be a question.
    """
    return "?" in text[10:]


def process_translation(text: str) -> Optional[str]:
    """
    Process the translation of the provided text using OpenAI.
    """
    try:
        start_time = time.time()
        response = openai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a translation assistant. Translate the following text to English. "
                        "The transcription might not be perfect, so try to understand the meaning. "
                        "Respond with the translated text only."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=150,
        )
        translation = response.choices[0].message.content.strip()
        logging.info("Translation Time: %.2f seconds", time.time() - start_time)
        logging.info("Translation: %s", translation)
        return translation
    except Exception as e:
        logging.error("Error translating text: %s", e)
        return None


def process_question(text: str, context_container: Dict[str, str]) -> Optional[str]:
    """
    Process a question from the given text and update the context.
    If a question is detected, generate an answer and clear the context.
    Otherwise, accumulate the text as context.
    """
    try:
        if is_question(text):
            answer = answer_question(context_container.get("text", ""), text)
            return answer
        else:
            return None
    except Exception as e:
        logging.error("Error processing question: %s", e)
        return None


def parse_path(path: str) -> Tuple[str, str]:
    """
    Parse the URL path to extract the task and language.
    Expected path formats:
      - /klugscheiser
      - /translation/ru
    If language is not provided, a default is used.
    """
    parts = path.strip("/").split("/")
    task = parts[0] if parts and parts[0] else "klugscheiser"
    language = parts[1] if len(parts) > 1 else "ru"
    return task, language


async def handle_client(webskt: websockets.WebSocketServerProtocol) -> None:
    """
    Handle an individual client connection:
      - Extracts configuration from the URL path.
      - Receives audio chunks (raw bytes) over the WebSocket.
      - Feeds audio to Deepgram for transcription.
      - When a final transcription is received, processes it and sends the result back.
    """
    client_addr = webskt.remote_address
    task, language = parse_path(webskt.request.path)
    logging.info(
        "Client %s connected with task=%s, language=%s", client_addr, task, language
    )

    # Create a Deepgram connection for this client.
    dg_client = DeepgramClient()
    dg_connection = dg_client.listen.websocket.v("1")
    context_container: Dict[str, str] = {"text": ""}
    answer_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_open(self, open, **kwargs):
        logging.info("Connection Open")

    def on_message(self, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript
        if not sentence:
            return
        if result.is_final:
            logging.info("Final transcript from %s: %s", client_addr, sentence)
            if task == "klugscheiser":
                answer = process_question(sentence, context_container)
                context_container["text"] = (
                    context_container.get("text", "") + sentence + " "
                )
                if answer:
                    loop.call_soon_threadsafe(
                        answer_queue.put_nowait, json.dumps({"answer": answer})
                    )
            elif task == "translation":
                translation = process_translation(sentence)
                if translation:
                    loop.call_soon_threadsafe(
                        answer_queue.put_nowait,
                        json.dumps({"translation": translation}),
                    )
            else:
                logging.error("Invalid task specified: %s", task)
        else:
            logging.info("Interim transcript from %s: %s", client_addr, sentence)

    def on_metadata(self, metadata, **kwargs):
        logging.info("Metadata: %s", metadata)

    def on_speech_started(self, speech_started, **kwargs):
        logging.info("Speech Started")

    def on_close(self, close, **kwargs):
        logging.info("Connection Closed")

    def on_error(self, error, **kwargs):
        logging.error("Handled Error: %s", error)

    def on_unhandled(self, unhandled, **kwargs):
        logging.warning("Unhandled Websocket Message: %s", unhandled)

    # Register callbacks.
    dg_connection.on(LiveTranscriptionEvents.Open, on_open)
    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
    dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
    dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
    dg_connection.on(LiveTranscriptionEvents.Close, on_close)
    dg_connection.on(LiveTranscriptionEvents.Error, on_error)
    dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

    # Configure Deepgram options based on client configuration.
    options = LiveOptions(
        smart_format=True,
        encoding="linear16",
        channels=1,
        # sample_rate=16000,
        sample_rate=48000,
        interim_results=True,
        utterance_end_ms="1000",
        vad_events=True,
        endpointing=100,
    )
    if task == "klugscheiser":
        options.model = "nova-3"
        options.language = "en-US"
        logging.info("Operating in Klugscheiser mode for client %s", client_addr)
    elif task == "translation":
        options.model = "nova-2"
        options.language = language
        logging.info(
            "Operating in Translation mode from %s for client %s", language, client_addr
        )
    else:
        logging.error("Invalid task specified: %s", task)
        return

    addons = {"no_delay": "true"}
    if not dg_connection.start(options, addons=addons):
        logging.error("Failed to start Deepgram connection for client %s", client_addr)
        return

    try:
        async for msg in webskt:
            # If message is binary, treat it as an audio chunk.
            if isinstance(msg, bytes):
                dg_connection.send(msg)
            else:
                logging.info("Received non-binary message from %s", client_addr)

            # Send any available results from Deepgram to the client.
            while not answer_queue.empty():
                reply = await answer_queue.get()
                await webskt.send(reply)
    except websockets.exceptions.ConnectionClosed:
        logging.info("Client %s disconnected", client_addr)
    finally:
        dg_connection.finish()
        logging.info("Cleaned up Deepgram connection for client: %s", client_addr)


async def handle_client_html(request):
    html_path = os.path.join(os.path.dirname(__file__), "client.html")
    return web.FileResponse(html_path)


async def start_http_server(
    http_port: int, ssl_context: Optional[ssl.SSLContext] = None
):
    app = web.Application()
    app.router.add_get("/", handle_client_html)
    runner = web.AppRunner(app)
    await runner.setup()
    # Pass ssl_context to the TCPSite for https.
    site = web.TCPSite(runner, "0.0.0.0", http_port, ssl_context=ssl_context)
    await site.start()
    logging.info(
        "HTTP Server started at %s://localhost:%s",
        "https" if ssl_context else "http",
        http_port,
    )
    while True:
        await asyncio.sleep(3600)


async def main(
    ws_port: int, http_port: int, ssl_context: Optional[ssl.SSLContext] = None
) -> None:
    # Start both WebSocket and HTTP servers concurrently,
    # pass ssl_context to enable https and secure websockets (wss) on both.
    ws_server = websockets.serve(handle_client, "0.0.0.0", ws_port, ssl=ssl_context)
    await asyncio.gather(ws_server, start_http_server(http_port, ssl_context))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audio processing server")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket port")
    parser.add_argument("--http-port", type=int, default=8001, help="HTTP port")
    # New arguments for SSL certificate and key.
    parser.add_argument(
        "--ssl-cert", type=str, help="Path to SSL certificate file", default=None
    )
    parser.add_argument(
        "--ssl-key", type=str, help="Path to SSL key file", default=None
    )
    args = parser.parse_args()

    # Create SSL context if both certificate and key are provided.
    ssl_context = None
    if args.ssl_cert and args.ssl_key:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        try:
            ssl_context.load_cert_chain(certfile=args.ssl_cert, keyfile=args.ssl_key)
        except PermissionError as e:
            logging.error("Permission denied loading SSL certificate: %s", e)
            import sys

            sys.exit(1)

    try:
        asyncio.run(main(args.ws_port, args.http_port, ssl_context))
    except KeyboardInterrupt:
        logging.info("Server shutdown via KeyboardInterrupt")
