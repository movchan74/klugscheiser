#!/usr/bin/env python
"""
Server for processing audio streams via Deepgram and OpenAI.
It accepts audio chunks over a WebSocket, transcribes them using Deepgram,
and returns processed results (a translation or an answer) to the client.
Configuration (task and language) is provided in the connection URL path.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import websockets
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
        sample_rate=16000,
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


async def main() -> None:
    server = await websockets.serve(handle_client, "localhost", 8765)
    logging.info("WebSocket Server started at ws://localhost:8765")
    await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutdown via KeyboardInterrupt")
