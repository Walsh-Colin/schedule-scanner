"""Created after the packaged launcher selects its private local endpoint."""
from ollama import Client

client = Client(trust_env=False)
