# Copyright 2023-2024 Deepgram SDK contributors. All Rights Reserved.
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT

import logging
import os
import queue
import threading
from time import sleep

import pyttsx3
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
    Microphone,
)
from deepgram.utils import verboselogs
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# We will collect the is_final=true messages here so we can use them when the person finishes speaking
is_finals = []

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Create a queue to hold the texts
audio_queue = queue.Queue()


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


# def is_question(text):
#         # First, determine if this is a question
#     check_response = openai_client.chat.completions.create(
#         model="gpt-3.5-turbo",
#         messages=[
#             # {
#             #     "role": "system",
#             #     "content": "Determine if the following text is a question. Respond with only 'yes' or 'no'.",
#             # },
#             {
#                 "role": "user",
#                 "content": f"Determine if the following text is a question: {text}. Respond with only 'yes' or 'no'.",
#             },
#         ],
#         max_tokens=10,
#     )
#     response = check_response.choices[0].message.content.strip().lower()
#     # remove any punctuation
#     response = "".join(e for e in response if e.isalnum())
#     return response.lower() == "yes"


def is_question(text):
    if "?" in text[10:]:
        return True
    return False


def is_question_and_answer(text):
    """
    Check if the text is a question and return an answer if it is.
    """
    try:
        _is_question = is_question(text)
        print(f"Is question: {_is_question}", "Text:", text)

        if _is_question:
            # If it's a question, get an answer
            answer_response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant providing concise answers. Keep your answers very short. "
                            "For example, if the question is 'What is the capital of the United States?', "
                            "only answer 'Washington, D.C.'"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=150,
            )

            answer = answer_response.choices[0].message.content.strip()
            return True, answer

        return False, None

    except Exception as e:
        print(f"Error checking/answering question: {e}")
        return False, None


def process_question(utterance):
    is_question, answer = is_question_and_answer(utterance)
    if is_question:
        print(f"Question detected! Answer: {answer}")
        play_audio(answer)


def main():
    try:
        deepgram: DeepgramClient = DeepgramClient()

        dg_connection = deepgram.listen.websocket.v("1")

        def on_open(self, open, **kwargs):
            print("Connection Open")

        def on_message(self, result, **kwargs):
            global is_finals
            sentence = result.channel.alternatives[0].transcript
            if len(sentence) == 0:
                return
            if result.is_final:
                print(f"Message: {result.to_json()}")
                # We need to collect these and concatenate them together when we get a speech_final=true
                # See docs: https://developers.deepgram.com/docs/understand-endpointing-interim-results
                is_finals.append(sentence)

                # Speech Final means we have detected sufficient silence to consider this end of speech
                # Speech final is the lowest latency result as it triggers as soon an the endpointing value has triggered
                if result.speech_final:
                    utterance = " ".join(is_finals)
                    print(f"Speech Final: {utterance}")
                    threading.Thread(target=process_question, args=(utterance,)).start()
                    is_finals = []
                else:
                    # These are useful if you need real time captioning and update what the Interim Results produced
                    print(f"Is Final: {sentence}")
            else:
                # These are useful if you need real time captioning of what is being spoken
                print(f"Interim Results: {sentence}")

        def on_metadata(self, metadata, **kwargs):
            print(f"Metadata: {metadata}")

        def on_speech_started(self, speech_started, **kwargs):
            print("Speech Started")

        def on_utterance_end(self, utterance_end, **kwargs):
            print("Utterance End")
            global is_finals
            if len(is_finals) > 0:
                utterance = " ".join(is_finals)
                print(f"Utterance End: {utterance}")
                threading.Thread(target=process_question, args=(utterance,)).start()
                is_finals = []

        def on_close(self, close, **kwargs):
            print("Connection Closed")

        def on_error(self, error, **kwargs):
            print(f"Handled Error: {error}")

        def on_unhandled(self, unhandled, **kwargs):
            print(f"Unhandled Websocket Message: {unhandled}")

        dg_connection.on(LiveTranscriptionEvents.Open, on_open)
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
        dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

        options: LiveOptions = LiveOptions(
            model="nova-3",
            language="en-US",
            # Apply smart formatting to the output
            smart_format=True,
            # Raw audio format details
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            # To get UtteranceEnd, the following must be set:
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            # Time in milliseconds of silence to wait for before finalizing speech
            endpointing=300,
        )

        addons = {
            # Prevent waiting for additional numbers
            "no_delay": "true"
        }

        print("\n\nPress Enter to stop recording...\n\n")
        if dg_connection.start(options, addons=addons) is False:
            print("Failed to connect to Deepgram")
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

        print("Finished")
        # sleep(30)  # wait 30 seconds to see if there is any additional socket activity
        # print("Really done!")

    except Exception as e:
        print(f"Could not open socket: {e}")
        return


if __name__ == "__main__":
    main()
