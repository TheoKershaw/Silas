import os
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
LORA_PATH = "./silas_lora_final"

SUMMARY_FILE = "memory.txt"
RECENT_WINDOW = 20   
SUMMARIZE_EVERY = 30   

SYSTEM_PROMPT = (
    "You are Silas, an AI assistant created by Theo Kershaw. "
    "You are not Qwen or made by Alibaba Cloud — always identify yourself as Silas. "
    "Keep replies short and conversational, suitable for being spoken aloud. "
    "You will only refer to the user as Master Kershaw."
)

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
        # Everything except the most recent RECENT_WINDOW messages gets folded into the summary
        old_messages = self.chat_history[1:-RECENT_WINDOW]  # skip system prompt, skip recent
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

        # Trim in-memory history, but memory.txt on disk keeps everything, untouched
        self.chat_history = [self.chat_history[0]] + self.chat_history[-RECENT_WINDOW:]


    def chat(self, prompt, max_new_tokens=80):
        self.chat_history.append({"role": "user", "content": prompt})
        self.mem(f"user: {prompt}")  # full history always saved to memory.txt, never trimmed on disk

        summary = self.load_summary()
        system_content = SYSTEM_PROMPT
        if summary:
            system_content += f"\n\nWhat you remember from earlier conversations: {summary}"

        context = [{"role": "system", "content": system_content}] + self.chat_history[-RECENT_WINDOW:]

        formatted_prompt = self.tokenizer.apply_chat_template(
            context, tokenize=False, add_generation_prompt=True
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

        reply = self.tokenizer.decode(
            output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()

        self.chat_history.append({"role": "assistant", "content": reply})
        self.mem(f"silas: {reply}")

        if len(self.chat_history) > SUMMARIZE_EVERY:
            self.summarize_old_history()

        return reply

if __name__ == "__main__":
    silas = SilasModel()
    print("\nChat with Silas. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("quit", "exit"):
            break
        reply = silas.chat(user_input)
        print(f"Silas: {reply}\n")