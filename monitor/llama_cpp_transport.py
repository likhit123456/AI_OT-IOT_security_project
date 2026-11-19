"""Translate observer requests to the local llama.cpp OpenAI chat endpoint."""
from __future__ import annotations

from monitor.observer import _http_post_json


def llama_cpp_post(url, body, timeout):
    options = body.get("options", {})
    request = {"model": body["model"], "messages": body["messages"], "stream": False,
               "temperature": options.get("temperature", 0), "max_tokens": options.get("num_predict", 128),
               "repeat_penalty": options.get("repeat_penalty", 1.1),
               "repeat_last_n": options.get("repeat_last_n", 3072), "seed": options.get("seed", 3407),
               "cache_prompt": False}
    if body.get("format"):
        request["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "observer_verdict", "strict": True, "schema": body["format"]}}
    root = url.removesuffix("/api/chat")
    response = _http_post_json(root+"/v1/chat/completions", request, timeout)
    choice = response["choices"][0]
    usage = response.get("usage", {})
    return {"model": response.get("model"), "message": choice["message"],
            "done_reason": "length" if choice.get("finish_reason") == "length" else "stop",
            "prompt_eval_count": usage.get("prompt_tokens"), "eval_count": usage.get("completion_tokens"),
            "timings": response.get("timings"), "backend": "llama.cpp"}
