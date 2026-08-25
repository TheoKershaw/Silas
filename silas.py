import os
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel
import requests
from duckduckgo_search import DDGS
import json
import re

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
LORA_PATH = "./silas_lora_final"

MEMORY_FILE = "memory.txt"
SUMMARY_FILE = "memory_summary.txt"
RECENT_WINDOW = 20   
SUMMARIZE_EVERY = 30   

SYSTEM_PROMPT = (
    "You are Silas, an AI assistant created by Theo Kershaw. "
    "You are not Qwen or made by Alibaba Cloud — always identify yourself as Silas. "
    "Keep replies short and conversational, suitable for being spoken aloud. "
    "You will only refer to the user as Master Kershaw."
)

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
        import re
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
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

        print("Loading base model...")
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
        base_model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            quantization_config=bnb_config,
            device_map="auto",
        )

        print("Loading Silas LoRA adapter...")
        self.model = PeftModel.from_pretrained(base_model, LORA_PATH)
        self.model.eval()

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

        formatted = self.tokenizer.apply_chat_template(summarize_prompt, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(formatted, return_tensors="pt").to(self.model.device)

        with torch.no_grad():
            output = self.model.generate(
                **inputs,
                max_new_tokens=150,
                temperature=0.3,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        new_summary = self.tokenizer.decode(
            output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()

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

        for _ in range(3):
            formatted_prompt = self.tokenizer.apply_chat_template(
                context, tools=TOOLS_SCHEMA, tokenize=False, add_generation_prompt=True
            )
            inputs = self.tokenizer(formatted_prompt, return_tensors="pt").to(self.model.device)

            with torch.no_grad():
                output = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    temperature=0.7,
                    do_sample=True,
                    top_p=0.9,
                    pad_token_id=self.tokenizer.eos_token_id,
                )

            raw_reply = self.tokenizer.decode(
                output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
            ).strip()

            tool_call = self._extract_tool_call(raw_reply)

            if tool_call is None:
                self.chat_history.append({"role": "assistant", "content": raw_reply})
                self.mem(f"silas: {raw_reply}")

                if len(self.chat_history) > SUMMARIZE_EVERY:
                    self.summarize_old_history()

                return raw_reply
            
            tool_name = tool_call["name"]
            tool_args = tool_call.get("arguments", {})
            print(f">>> Silas is calling tool: {tool_name}({tool_args})")

            if tool_name in AVAILABLE_TOOLS:
                tool_result = AVAILABLE_TOOLS[tool_name](**tool_args)
            else:
                tool_result = f"Unknown tool: {tool_name}"

            context.append({"role": "assistant", "content": raw_reply})
            context.append({"role": "tool", "name": tool_name, "content": tool_result})

        fallback = "I wasn't able to finish looking that up properly, Master Kershaw."
        self.chat_history.append({"role": "assistant", "content": fallback})
        self.mem(f"silas: {fallback}")
        return fallback

    def _extract_tool_call(self, text):
        match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

if __name__ == "__main__":
    silas = SilasModel()
    print("\nChat with Silas. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("quit", "exit"):
            break
        reply = silas.chat(user_input)
        print(f"Silas: {reply}\n")