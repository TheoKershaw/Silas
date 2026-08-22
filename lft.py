import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset
from trl import SFTTrainer

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,
)

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    quantization_config=bnb_config,
    device_map="auto",
)

model = prepare_model_for_kbit_training(model)

lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
)
model = get_peft_model(model, lora_config)

dataset = load_dataset("json", data_files="silas_finetune.jsonl", split="train")

def format_example(example):
    return tokenizer.apply_chat_template(example["messages"], tokenize=False)

training_args = TrainingArguments(
    output_dir="./silas_lora",
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    num_train_epochs=3,
    learning_rate=2e-4,
    logging_steps=5,
    save_strategy="epoch",
    bf16=True,
    optim="paged_adamw_8bit",
)

trainer = SFTTrainer(
    model=model,
    args=training_args,
    train_dataset=dataset,
    formatting_func=format_example,
)

trainer.train()
model.save_pretrained("./silas_lora_final")
tokenizer.save_pretrained("./silas_lora_final")
print("Done. LoRA adapter saved to ./silas_lora_final")