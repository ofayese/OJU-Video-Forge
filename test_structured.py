import json
import subprocess

schema = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "audience": {"type": "string"},
        "learning_objectives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["text", "kind", "evidence"]
            }
        },
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "start_ms": {"type": "integer"},
                    "end_ms": {"type": "integer"},
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["id", "start_ms", "end_ms", "text", "kind", "evidence"]
            }
        },
        "script": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "start_ms": {"type": "integer"},
                    "end_ms": {"type": "integer"},
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["id", "start_ms", "end_ms", "text", "kind", "evidence"]
            }
        }
    },
    "required": ["title", "audience", "learning_objectives", "chapters", "script"]
}

prompt = "Create a minimal training draft for a 5 second video showing a blue screen. Evidence: frame-0. Return only the JSON."

result = subprocess.run([
    "docker", "exec", "oju-video-forge-ollama-1", "ollama", "run", "qwen3-vl:4b",
    "--format", json.dumps(schema),
    prompt
], capture_output=True, text=True, timeout=180)

print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("Return code:", result.returncode)