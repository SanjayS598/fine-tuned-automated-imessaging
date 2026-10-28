"""Interactive training script for fine-tuning models on iMessage data"""
import subprocess
import sys
import json
from pathlib import Path

# Default training configurations based on model size patterns
# These are used as fallbacks if model not in config
DEFAULT_CONFIGS = {
    "small": {"iters": 500, "batch_size": 2, "max_seq": 2048},   # < 1B params
    "medium": {"iters": 500, "batch_size": 2, "max_seq": 2048},  # 1-3B params
    "large": {"iters": 500, "batch_size": 1, "max_seq": 2048},   # 3-7B params
    "xlarge": {"iters": 500, "batch_size": 1, "max_seq": 1024},  # 7B+ params
}

# Model-specific configurations (override defaults)
# Add your models here for custom training params
MODEL_CONFIGS = {
    "mlx-community/Qwen2.5-7B-Instruct-4bit": {"iters": 500, "batch_size": 1, "max_seq": 1024},
}

def get_model_slug(model_path):
    """Convert model path to safe folder name"""
    return model_path.replace("/", "_").replace(".", "_")

def discover_mlx_models():
    """Discover all downloaded MLX models from HF cache"""
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"

    if not hf_cache.exists():
        return []

    models = []
    for model_dir in hf_cache.iterdir():
        if model_dir.is_dir() and model_dir.name.startswith("models--"):
            # Extract model name from directory
            # Format: models--org--model-name
            parts = model_dir.name.replace("models--", "").split("--")
            if len(parts) >= 2:
                model_name = "/".join(parts)

                # Check if it has a snapshots directory with actual model files
                snapshots = model_dir / "snapshots"
                if snapshots.exists():
                    # Check if any snapshot has model files
                    for snapshot in snapshots.iterdir():
                        if snapshot.is_dir():
                            # Look for model config or weights
                            if (snapshot / "config.json").exists() or list(snapshot.glob("*.safetensors")):
                                models.append(model_name)
                                break

    return sorted(set(models))

def guess_model_size(model_name):
    """Guess model size category from name"""
    lower = model_name.lower()

    # Check for size indicators
    if any(x in lower for x in ["0.5b", "0.6b", "500m", "600m"]):
        return "small"
    elif any(x in lower for x in ["1b", "1.5b", "1.7b", "1000m", "1500m", "1700m"]):
        return "medium"
    elif any(x in lower for x in ["2b", "3b", "4b", "2000m", "3000m", "4000m"]):
        return "large"
    elif any(x in lower for x in ["7b", "8b", "13b", "7000m", "8000m"]) or "4bit" in lower:
        return "xlarge"

    # Default to medium if can't determine
    return "medium"

def get_model_config(model_name):
    """Get training config for a model"""
    # Check if model has specific config
    if model_name in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_name]

    # Otherwise, guess based on size
    size_category = guess_model_size(model_name)
    return DEFAULT_CONFIGS[size_category]

def select_model():
    """Interactive model selection"""
    models = discover_mlx_models()

    if not models:
        print("❌ No MLX models found in HuggingFace cache!")
        print("\n💡 Download a model first:")
        print("   Example: huggingface-cli download Qwen/Qwen3-1.7B")
        print("   Or just start training and MLX will download it automatically")
        print("\n   Alternatively, enter a model name manually below.")

        manual = input("\nEnter model name (or press Enter to exit): ").strip()
        if not manual:
            sys.exit(1)
        return manual

    print("\n🤖 Available Downloaded Models:\n")
    for i, model in enumerate(models, 1):
        config = get_model_config(model)
        size_cat = guess_model_size(model)
        print(f"  [{i}] {model}")
        print(f"      ({size_cat}: iters={config['iters']}, batch={config['batch_size']}, seq={config['max_seq']})")

    print(f"\n  [{len(models) + 1}] Enter model name manually")

    while True:
        try:
            choice = input(f"\nSelect a model (1-{len(models) + 1}): ").strip()
            if not choice:
                continue

            idx = int(choice) - 1

            if idx == len(models):
                # Manual entry
                manual = input("Enter model name (e.g., Qwen/Qwen3-1.7B): ").strip()
                if manual:
                    return manual
                continue

            if 0 <= idx < len(models):
                return models[idx]

            print("❌ Invalid selection.")
        except ValueError:
            print("❌ Please enter a number.")
        except KeyboardInterrupt:
            print("\n\n👋 Cancelled.")
            sys.exit(0)

def main():
    # Check if data exists
    data_dir = Path("data_set")
    if not data_dir.exists() or not (data_dir / "train.jsonl").exists():
        print("❌ No training data found!")
        print("   Run: uv run python data_collection/parser.py")
        sys.exit(1)

    # Select model
    model = select_model()
    config = get_model_config(model)

    # Create model-specific adapter directory
    adapter_dir = Path("adapters") / get_model_slug(model)
    adapter_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "="*60)
    print(f"🎓 Training Configuration")
    print("="*60)
    print(f"Model:          {model}")
    print(f"Iterations:     {config['iters']}")
    print(f"Batch Size:     {config['batch_size']}")
    print(f"Max Seq Length: {config['max_seq']}")
    print(f"Adapter Path:   {adapter_dir}")
    print("="*60)

    # Confirm
    try:
        confirm = input("\nStart training? [Y/n]: ").strip().lower()
        if confirm and confirm != 'y':
            print("👋 Cancelled.")
            sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled.")
        sys.exit(0)

    # Build command
    cmd = [
        "uv", "run", "python", "-m", "mlx_lm.lora",
        "--model", model,
        "--train",
        "--data", "data_set/",
        "--iters", str(config['iters']),
        "--adapter-path", str(adapter_dir),
        "--batch-size", str(config['batch_size']),
        "--max-seq-length", str(config['max_seq'])
    ]

    print("\n🚀 Starting training...\n")

    # Run training
    try:
        subprocess.run(cmd, check=True)
        print("\n✅ Training complete!")
        print(f"   Adapters saved to: {adapter_dir}")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Training failed with exit code {e.returncode}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Training interrupted by user")
        sys.exit(1)

if __name__ == "__main__":
    main()
