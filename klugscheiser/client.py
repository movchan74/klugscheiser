#!/usr/bin/env python
"""
Client for capturing microphone audio, sending it to the server, and performing TTS on responses.
It reads audio data from the microphone, sends audio chunks over a WebSocket,
and uses pyttsx3 to vocalize the server’s answer or translation.
Configuration (task and language) is provided in the connection URL.
"""

import argparse
import asyncio
import json
import logging
import queue

import pyttsx3
import sounddevice as sd
import websockets

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Audio configuration.
CHUNK_SIZE = 1024
SAMPLE_RATE = 16000

# Queue to hold captured audio chunks.
audio_queue: queue.Queue = queue.Queue()


def audio_callback(indata, frames, time_info, status) -> None:
    """
    Callback for sounddevice InputStream.
    Converts the incoming audio data to bytes and puts it on a thread-safe queue.
    """
    if status:
        logging.warning("Audio input status: %s", status)
    audio_queue.put(indata.copy().tobytes())


def tts_play(text: str) -> None:
    """
    Play the provided text using pyttsx3 text-to-speech.
    """
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()
    engine.stop()


async def send_audio(task: str, language: str) -> None:
    # Construct the WebSocket URI based on configuration.
    # For example, ws://localhost:8765/klugscheiser or ws://localhost:8765/translation/ru
    if task == "translation":
        uri = f"ws://localhost:8765/{task}/{language}"
    else:
        uri = f"ws://localhost:8765/{task}"
    async with websockets.connect(uri) as ws:
        logging.info("Connected to %s", uri)
        # Start the microphone input stream.
        stream = sd.InputStream(
            callback=audio_callback,
            channels=1,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        logging.info("Microphone stream started")
        try:
            while True:
                # Send available audio chunks.
                try:
                    chunk = audio_queue.get_nowait()
                    await ws.send(chunk)
                except queue.Empty:
                    await asyncio.sleep(0.01)

                # Non-blocking check for a server reply.
                try:
                    reply = await asyncio.wait_for(ws.recv(), timeout=0.01)
                    data = json.loads(reply)
                    if "answer" in data:
                        logging.info("Received answer: %s", data["answer"])
                        tts_play(data["answer"])
                    elif "translation" in data:
                        logging.info("Received translation: %s", data["translation"])
                        tts_play(data["translation"])
                except asyncio.TimeoutError:
                    continue
        except websockets.exceptions.ConnectionClosed:
            logging.info("Server disconnected")
        finally:
            stream.stop()
            logging.info("Microphone stream stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Client for audio streaming and TTS with configuration in the URL."
    )
    parser.add_argument(
        "--task",
        default="klugscheiser",
        choices=["klugscheiser", "translation"],
        help="Task type: 'klugscheiser' for Q&A or 'translation' to translate speech to English.",
    )
    parser.add_argument(
        "--language",
        default="ru",
        help="Language code for translation mode (ignored for klugscheiser mode).",
    )
    args = parser.parse_args()

    asyncio.run(send_audio(args.task, args.language))
    input("")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Client terminated via KeyboardInterrupt")
