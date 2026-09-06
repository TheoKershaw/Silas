import os
import json
import re
import requests
from duckduckgo_search import DDGS
import ollama
import time

# ---- Config ----
# Run `ollama pull qwen2.5:7b` (or any model you like) before running this script.
OLLAMA_MODEL = "qwen2.5:7b"

SUMMARY_FILE = "summary_memory.txt"
RECENT_WINDOW = 20
SUMMARIZE_EVERY = 30

SYSTEM_PROMPT = (
    "You are Silas, an AI assistant created by Theo Kershaw. "
    "You are not Qwen or made by Alibaba Cloud — always identify yourself as Silas. "
    "Keep replies short and conversational, suitable for being spoken aloud. "
    "You will only refer to the user as Master Kershaw."
)

# ---- Tools ----

def tool_web_search(query, max_results=4):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No results found."
        return "\n\n".join(f"{r['title']}: {r['body']}\nSource: {r['href']}" for r in results)
    except Exception as e:
        return f"Search failed: {e}"

def tool_fetch_url(url):
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        text = resp.text
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:3000]
    except Exception as e:
        return f"Fetch failed: {e}"

AVAILABLE_TOOLS = {
    "web_search": tool_web_search,
    "fetch_url": tool_fetch_url,
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information, news, facts, or anything not known ahead of time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and read the text content of a specific web page URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full URL to fetch"}
                },
                "required": ["url"],
            },
        },
    },
]


class SilasModel:
    def __init__(self):
        print(f"Using Ollama model: {OLLAMA_MODEL}")
        print("(Make sure `ollama serve` is running and you've run `ollama pull {}` at least once)".format(OLLAMA_MODEL))

        self.chat_history = [{"role": "system", "content": SYSTEM_PROMPT}] + self.load_memory()
        print("Silas model ready.")

    def load_memory(self):
        history = []
        if os.path.exists("memory.txt"):
            with open("memory.txt", "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("user:"):
                        history.append({"role": "user", "content": line[len("user:"):].strip()})
                    elif line.startswith("silas:"):
                        history.append({"role": "assistant", "content": line[len("silas:"):].strip()})
        return history

    def mem(self, text):
        with open("memory.txt", "a") as f:
            f.write(text + "\n")

    def load_summary(self):
        if os.path.exists(SUMMARY_FILE):
            with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def save_summary(self, summary):
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            f.write(summary)

    def summarize_old_history(self):
        old_messages = self.chat_history[1:-RECENT_WINDOW]
        if not old_messages:
            return

        existing_summary = self.load_summary()

        transcript = "\n".join(
            f"{m['role']}: {m['content']}" for m in old_messages
        )

        summarize_prompt = [
            {"role": "system", "content": "Summarize the key facts, names, and context from this conversation in a few short sentences. Be concise, keep only what's important to remember long-term."},
            {"role": "user", "content": f"Existing summary:\n{existing_summary}\n\nNew conversation to fold in:\n{transcript}"}
        ]

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=summarize_prompt,
            options={"temperature": 0.3, "num_predict": 150},
        )
        new_summary = response["message"]["content"].strip()

        self.save_summary(new_summary)

        self.chat_history = [self.chat_history[0]] + self.chat_history[-RECENT_WINDOW:]

    def chat(self, prompt, max_new_tokens=300):
        self.chat_history.append({"role": "user", "content": prompt})
        self.mem(f"user: {prompt}")

        summary = self.load_summary()
        system_content = SYSTEM_PROMPT
        if summary:
            system_content += f"\n\nWhat you remember from earlier conversations: {summary}"

        context = [{"role": "system", "content": system_content}] + self.chat_history[-RECENT_WINDOW:]

        for attempt in range(3):
            gen_tokens = 80 if attempt < 2 else max_new_tokens

            start = time.time()
            response = ollama.chat(
                model=OLLAMA_MODEL,
                messages=context,
                tools=TOOLS_SCHEMA,
                options={"temperature": 0.7, "top_p": 0.9, "num_predict": gen_tokens},
            )
            elapsed = time.time() - start

            message = response["message"]
            tool_calls = message.get("tool_calls")

            eval_count = response.get("eval_count", 0)
            tok_per_sec = eval_count / elapsed if elapsed > 0 else 0
            print(f">>> Generated {eval_count} tokens in {elapsed:.1f}s ({tok_per_sec:.1f} tok/s)")

            if not tool_calls:
                raw_reply = message["content"].strip()
                self.chat_history.append({"role": "assistant", "content": raw_reply})
                self.mem(f"silas: {raw_reply}")

                if len(self.chat_history) > SUMMARIZE_EVERY:
                    self.summarize_old_history()

                return raw_reply

            # Handle tool call(s) - Ollama returns structured tool_calls, no manual parsing needed
            context.append(message)

            for call in tool_calls:
                tool_name = call["function"]["name"]
                tool_args = call["function"].get("arguments", {})
                print(f">>> Silas is calling tool: {tool_name}({tool_args})")

                if tool_name in AVAILABLE_TOOLS:
                    tool_result = AVAILABLE_TOOLS[tool_name](**tool_args)
                else:
                    tool_result = f"Unknown tool: {tool_name}"

                context.append({"role": "tool", "content": tool_result})

        fallback = "I wasn't able to finish looking that up properly, Master Kershaw."
        self.chat_history.append({"role": "assistant", "content": fallback})
        self.mem(f"silas: {fallback}")
        return fallback


if __name__ == "__main__":
    silas = SilasModel()
    print("\nChat with Silas. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("quit", "exit"):
            break
        reply = silas.chat(user_input)
        print(f"Silas: {reply}\n")