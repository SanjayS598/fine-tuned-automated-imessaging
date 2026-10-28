"""uv run python chat.py"""
import sys
import os
import re
import shutil
from pathlib import Path
from mlx_lm import load, generate
from mlx_lm.sample_utils import make_sampler

# --- CONFIGURATION ---
ADAPTERS_ROOT = Path("adapters")
TEMP_ADAPTER_DIR = "_temp_adapter_loader"
MAX_HISTORY = 15

# System Prompt
SYSTEM_PROMPT = """You are a casual user in a group chat.
You should always respond to the user's message and carry the conversation forward.
Do not act like an AI assistant.
Be friendly and engaging."""

def get_model_slug(model_path):
    """Convert model path to safe folder name"""
    return model_path.replace("/", "_").replace(".", "_")

def slug_to_model_name(slug):
    """Convert slug back to model name (reverse of get_model_slug)"""
    # Replace underscores back to slashes and dots
    # This is a best-effort reconstruction
    parts = slug.split("_")

    # Common pattern: Org_ModelName or Org_Model_Name
    if len(parts) >= 2:
        # Try to find where the org/repo split is
        # Usually first underscore is the org separator
        org = parts[0]
        repo = "_".join(parts[1:])

        # Restore dots in version numbers (e.g., "2_5" -> "2.5")
        repo = re.sub(r'(\d)_(\d)', r'\1.\2', repo)

        return f"{org}/{repo}"

    return slug

def discover_mlx_models():
    """Discover all downloaded MLX models from HF cache"""
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"

    if not hf_cache.exists():
        return []

    models = []
    for model_dir in hf_cache.iterdir():
        if model_dir.is_dir() and model_dir.name.startswith("models--"):
            # Extract model name from directory
            parts = model_dir.name.replace("models--", "").split("--")
            if len(parts) >= 2:
                model_name = "/".join(parts)

                # Check if it has a snapshots directory with actual model files
                snapshots = model_dir / "snapshots"
                if snapshots.exists():
                    for snapshot in snapshots.iterdir():
                        if snapshot.is_dir():
                            if (snapshot / "config.json").exists() or list(snapshot.glob("*.safetensors")):
                                models.append(model_name)
                                break

    return sorted(set(models))

def find_trained_models():
    """Find all models that have been trained"""
    if not ADAPTERS_ROOT.exists():
        return []

    trained = []
    for item in ADAPTERS_ROOT.iterdir():
        if item.is_dir():
            # Check if it has adapter files
            adapter_files = list(item.glob("*.safetensors"))
            if adapter_files:
                # Reconstruct model name from slug
                model_name = slug_to_model_name(item.name)
                trained.append((model_name, item))

    return sorted(trained, key=lambda x: x[0])

def select_model():
    """Interactive model selection"""
    trained = find_trained_models()

    if not trained:
        print("❌ No trained models found!")
        print("   Run: uv run python train.py")
        sys.exit(1)

    print("\n🤖 Available Trained Models:\n")
    for i, (model, adapter_path) in enumerate(trained, 1):
        # Count checkpoints
        checkpoints = list(adapter_path.glob("*.safetensors"))
        print(f"  [{i}] {model}")
        print(f"      ({len(checkpoints)} checkpoint(s) in {adapter_path.name})")

    print(f"  [{len(trained) + 1}] Use base model (no fine-tuning)")

    while True:
        try:
            choice = input(f"\nSelect a model (1-{len(trained) + 1}): ").strip()
            if not choice:
                continue

            idx = int(choice) - 1

            if idx == len(trained):
                # Base model
                return select_base_model(), None

            if 0 <= idx < len(trained):
                return trained[idx]

            print("❌ Invalid selection.")
        except ValueError:
            print("❌ Please enter a number.")
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            sys.exit(0)

def select_base_model():
    """Select base model without adapters"""
    models = discover_mlx_models()

    if not models:
        print("\n❌ No models found in HuggingFace cache!")
        manual = input("Enter model name manually: ").strip()
        if manual:
            return manual
        sys.exit(1)

    print("\n🤖 Select Base Model:\n")
    for i, model in enumerate(models, 1):
        print(f"  [{i}] {model}")

    print(f"\n  [{len(models) + 1}] Enter model name manually")

    while True:
        try:
            choice = input(f"\nSelect a model (1-{len(models) + 1}): ").strip()
            if not choice:
                continue

            idx = int(choice) - 1

            if idx == len(models):
                manual = input("Enter model name: ").strip()
                if manual:
                    return manual
                continue

            if 0 <= idx < len(models):
                return models[idx]

            print("❌ Invalid selection.")
        except ValueError:
            print("❌ Please enter a number.")

def get_adapter_files(directory):
    """Scans the adapter directory for .safetensors files."""
    if not os.path.exists(directory):
        return []

    files = [f for f in os.listdir(directory) if f.endswith(".safetensors")]

    def sort_key(f):
        numbers = re.findall(r'\d+', f)
        if numbers:
            return int(numbers[0])
        return 999999999

    return sorted(files, key=sort_key)

def setup_temp_adapter(adapter_dir, selected_file):
    """Creates a temp directory with symbolic links to the selected checkpoint."""
    if os.path.exists(TEMP_ADAPTER_DIR):
        shutil.rmtree(TEMP_ADAPTER_DIR)
    os.makedirs(TEMP_ADAPTER_DIR)

    # 1. Link the Config
    config_src = os.path.join(adapter_dir, "adapter_config.json")
    config_dst = os.path.join(TEMP_ADAPTER_DIR, "adapter_config.json")
    if os.path.exists(config_src):
        os.symlink(os.path.abspath(config_src), config_dst)
    else:
        print("❌ Warning: adapter_config.json not found!")

    # 2. Link the Selected Weights -> adapters.safetensors
    weights_src = os.path.join(adapter_dir, selected_file)
    weights_dst = os.path.join(TEMP_ADAPTER_DIR, "adapters.safetensors")
    os.symlink(os.path.abspath(weights_src), weights_dst)

    return TEMP_ADAPTER_DIR

def cleanup_temp():
    """Removes the temporary folder."""
    if os.path.exists(TEMP_ADAPTER_DIR):
        shutil.rmtree(TEMP_ADAPTER_DIR)

def select_checkpoint(adapter_dir):
    """Interactive menu to select a checkpoint."""
    files = get_adapter_files(str(adapter_dir))

    if not files:
        print(f"❌ No adapter files found in {adapter_dir}")
        return None

    print("\n🔍 Found Checkpoints:")
    for i, f in enumerate(files):
        print(f"  [{i+1}] {f}")

    print(f"  [{len(files)+1}] None (Use Base Model)")

    while True:
        try:
            choice = input(f"\nSelect a checkpoint (1-{len(files)+1}): ").strip()
            if not choice: continue

            idx = int(choice) - 1

            if idx == len(files):
                return None

            if 0 <= idx < len(files):
                return files[idx]

            print("❌ Invalid selection.")
        except ValueError:
            print("❌ Please enter a number.")

def format_messages_manually(messages):
    """Format messages manually for models without chat templates"""
    formatted = ""
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            formatted += f"System: {content}\n\n"
        elif role == "user":
            formatted += f"User: {content}\n\n"
        elif role == "assistant":
            formatted += f"Assistant: {content}\n\n"

    # Add prompt for next assistant response
    formatted += "Assistant: "
    return formatted

def clean_response_text(text):
    """Remove thinking tags, speaker labels, and extra whitespace from model output."""
    # Remove <think>...</think> blocks (including multiline)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove any standalone <think> or </think> tags
    text = re.sub(r'</?think>', '', text)
    # Remove speaker labels (Dev, Friend, etc.) that appear at start of lines
    text = re.sub(r'^(Dev|Friend):\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n(Dev|Friend)\n', '\n', text)  # Remove standalone "Dev" lines
    text = re.sub(r'^(Dev|Friend)$', '', text, flags=re.MULTILINE)  # Remove lines with just the name
    # Clean up excessive whitespace
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Max 2 newlines
    text = text.strip()
    return text

def main():
    try:
        # Select model
        model, adapter_dir = select_model()

        load_path = None
        if adapter_dir:
            # Fine-tuned model - select checkpoint
            selected_file = select_checkpoint(adapter_dir)

            if selected_file:
                print("-" * 50)
                print(f"Preparing Checkpoint: {selected_file}")
                load_path = setup_temp_adapter(adapter_dir, selected_file)
                print(f"Loading from temp env: {load_path}")
            else:
                print("Loading Base Model (No Fine-Tuning)")
        else:
            print("Loading Base Model (No Fine-Tuning)")

        print("-" * 50)
        print(f"Model: {model}")
        print("-" * 50)

        # Load model
        model_obj, tokenizer = load(model, adapter_path=load_path)

        print("\n✅ Ready! Type 'quit' to exit.")

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        while True:
            try:
                user_input = input("\nYou: ")
            except KeyboardInterrupt:
                break

            if user_input.strip().lower() in ["quit", "exit"]:
                break

            if not user_input.strip():
                continue

            messages.append({"role": "user", "content": user_input})

            # Try to use chat template, fallback to manual formatting if not available
            try:
                if hasattr(tokenizer, 'chat_template') and tokenizer.chat_template is not None:
                    prompt = tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True
                    )
                else:
                    # Manual formatting for models without chat templates
                    prompt = format_messages_manually(messages)
            except (ValueError, AttributeError):
                # Fallback to manual formatting
                prompt = format_messages_manually(messages)

            # Create sampler (Controls "creativity")
            sampler = make_sampler(temp=0.6)

            response = generate(
                model_obj,
                tokenizer,
                prompt=prompt,
                max_tokens=150,
                verbose=False,
                sampler=sampler
            )

            # Clean the response to remove <think> tags and extra whitespace
            clean_response = clean_response_text(response)
            print(f"Bot: {clean_response}")

            messages.append({"role": "assistant", "content": clean_response})

            if len(messages) > (MAX_HISTORY * 2) + 1:
                messages.pop(1)
                messages.pop(1)

    finally:
        cleanup_temp()

if __name__ == "__main__":
    main()
