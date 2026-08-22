import os
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
LORA_PATH = "./silas_lora_final"

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

    def chat(self, prompt, max_new_tokens=200):
        self.chat_history.append({"role": "user", "content": prompt})
        self.mem(f"user: {prompt}")

        formatted_prompt = self.tokenizer.apply_chat_template(
            self.chat_history, tokenize=False, add_generation_prompt=True
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