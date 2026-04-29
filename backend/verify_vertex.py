"""
Quick sanity-check: verifies that the service-account JSON can reach Vertex AI
and get a response from gemini-2.5-flash.

Usage:
    python verify_vertex.py path\to\service_account.json
"""

import sys
import os

if len(sys.argv) < 2:
    print("Usage: python verify_vertex.py path\\to\\service_account.json")
    sys.exit(1)

key_file = sys.argv[1]
if not os.path.isfile(key_file):
    print(f"ERROR: file not found: {key_file}")
    sys.exit(1)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(key_file)

from google import genai

PROJECT = "bgu-lecture-vertex"
LOCATION = "us-central1"
MODEL = "gemini-2.5-flash"

print(f"Connecting to Vertex AI project={PROJECT} location={LOCATION} ...")

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

print(f"Sending test message to {MODEL} ...")
response = client.models.generate_content(
    model=MODEL,
    contents="Reply with exactly: CREDENTIALS_OK"
)

print("\n--- Model response ---")
print(response.text)
print("----------------------")
print("\nSuccess! Vertex AI credentials are working correctly.")
