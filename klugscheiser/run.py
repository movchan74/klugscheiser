import argparse  # New import
import logging
import os
import queue
import threading
import time

import pyttsx3
from deepgram import (
    DeepgramClient,
    LiveOptions,
    LiveTranscriptionEvents,
    Microphone,
)
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Configure logger with timestamp
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

previous_text = ""

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Create a queue to hold the texts
audio_queue = queue.Queue()

MODEL = "gpt-4o"


def audio_worker():
    engine = pyttsx3.init()
    while True:
        text = audio_queue.get()
        if text is None:
            break  # Exit the loop if None is received
        engine.say(text)
        engine.runAndWait()
        audio_queue.task_done()
    engine.stop()


# Start the audio thread
threading.Thread(target=audio_worker, daemon=True).start()


# Function to add text to the queue
def play_audio(text):
    audio_queue.put(text)


def process_translation(text):
    try:
        start_time = time.time()
        translation_response = openai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a translation assistant. Translate the following text to English. "
                        # "You are a translation assistant. Translate the following Chinese text to English. "
                        "The transcription might not be perfect, try to understand the meaning."
                        "Respond with the translated text only."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=150,
        )
        translation = translation_response.choices[0].message.content.strip()
        logging.info("Translation Time: %s", time.time() - start_time)
        logging.info("Translation: %s", translation)
        play_audio(translation)
    except Exception as e:
        logging.error("Error translating text: %s", e)


def answer_question(context, question):
    answer_response = openai_client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant providing concise answers. Keep your answers very short. "
                    "For example, if the question is 'What is the capital of the United States?', "
                    "only answer 'Washington, D.C.'. Use the context to understand the question."
                ),
            },
            {"role": "user", "content": f"Context: {context}\nQuestion: {question}"},
        ],
        max_tokens=150,
    )

    answer = answer_response.choices[0].message.content.strip()
    return answer


def is_question(text):
    if "?" in text[10:]:
        return True
    return False


def process_question(text):
    global previous_text
    try:
        _is_question = is_question(text)
        print(f"Is question: {_is_question}", "Text:", text)

        if _is_question:
            answer = answer_question(previous_text, text)
            print(f"Question detected! Answer: {answer}")
            play_audio(answer)
        else:
            print("Not a question")
            previous_text += text
    except Exception as e:
        print(f"Error checking/answering question: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run translation or klugscheiser.")
    parser.add_argument(
        "--task",
        default="klugscheiser",
        choices=["translation", "klugscheiser"],
        help="Task type.",
    )
    parser.add_argument("--language", default="ru", help="Language code.")
    args = parser.parse_args()

    task = args.task
    language = args.language

    try:
        deepgram: DeepgramClient = DeepgramClient()

        dg_connection = deepgram.listen.websocket.v("1")

        def on_open(self, open, **kwargs):
            logging.info("Connection Open")

        def on_message(self, result, **kwargs):
            # print(f"Message: {result.to_json()}")
            sentence = result.channel.alternatives[0].transcript
            if len(sentence) == 0:
                return
            if result.is_final:
                # print(f"Message: {result.to_json()}")
                logging.info("Speech Final: %s", sentence)
                if task == "klugscheiser":
                    threading.Thread(target=process_question, args=(sentence,)).start()
                elif task == "translation":
                    threading.Thread(
                        target=process_translation, args=(sentence,)
                    ).start()
                else:
                    logging.error("Invalid task")
            else:
                logging.info("Interim Results: %s", sentence)

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

        dg_connection.on(LiveTranscriptionEvents.Open, on_open)
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

        options: LiveOptions = LiveOptions(
            # model="nova-3",
            # model="nova-2",
            # language="en-US",
            # language="zh",
            # language="ru",
            # language="de",
            # Apply smart formatting to the output
            smart_format=True,
            # Raw audio format details
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            # To get UtteranceEnd, the following must be set:
            interim_results=True,
            # interim_results=False,
            utterance_end_ms="1000",
            # utterance_end_ms="500",
            vad_events=True,
            # Time in milliseconds of silence to wait for before finalizing speech
            endpointing=100,
            # endpointing=300,
        )

        if task == "klugscheiser":
            options.model = "nova-3"
            options.language = "en-US"
            logging.info("Klugscheiser mode")
        elif task == "translation":
            options.model = "nova-2"
            options.language = language
            logging.info(f"Translation mode from {language}")
        else:
            logging.error("Invalid task")
            return

        addons = {
            # Prevent waiting for additional numbers
            "no_delay": "true"
        }

        logging.info("\n\nPress Enter to stop recording...\n\n")
        if dg_connection.start(options, addons=addons) is False:
            logging.error("Failed to connect to Deepgram")
            return

        # Open a microphone stream on the default input device
        microphone = Microphone(dg_connection.send)

        # start microphone
        microphone.start()

        # wait until finished
        input("")

        # Wait for the microphone to close
        microphone.finish()

        # Indicate that we've finished
        dg_connection.finish()

        logging.info("Finished")

    except Exception as e:
        logging.error("Could not open socket: %s", e)
        return


if __name__ == "__main__":
    main()
