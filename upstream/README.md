# iMessage Chat Bot Fine-Tuning

Train a personalized chatbot on your own iMessage conversations using Qwen models and MLX.

## 🚀 Features

- Extract and parse iMessage conversations from macOS
- Fine-tune Qwen models on your chat history
- Interactive chat interface with conversation memory
- Support for multiple training checkpoints
- Clean, conversational responses

## 📋 Prerequisites

- **macOS** (for iMessage database access)
- **Python 3.12+**
- **Apple Silicon Mac** (for MLX framework)
- **uv** package manager ([install here](https://github.com/astral-sh/uv))

## 🛠️ Setup

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the Repository

```bash
git clone <your-repo-url>
cd
```

### 3. Copy Your iMessage Database

**Place `chat.db` in the `chat_db_goes_here/` folder:**

```bash
sudo cp ~/Library/Messages/chat.db ./chat_db_goes_here/
```

⚠️ **Note:** You may need to grant Terminal "Full Disk Access" in System Preferences → Security & Privacy → Privacy → Full Disk Access.

### 4. Run the Commands

**That's it! Now just run these three commands:**

```bash
# 1. Parse your messages (creates training data)
uv run python src/data_collection/parser.py

# 2. Train the model (interactive)
uv run python src/train.py
# → Shows downloaded models OR enter any HuggingFace model name
# → Example: "Qwen/Qwen3-1.7B" or "microsoft/Phi-3-mini-4k-instruct"

# 3. Chat with your bot (interactive)
uv run python src/chat.py
# → Select from your trained models & checkpoints
```

**Training** will show you all downloaded models, but you can also type in ANY HuggingFace model name to download and train it automatically!

**Chat** will show all your trained models and let you pick which checkpoint to use.

---

**Alternative: Manual Training Command**

If you prefer to run the training command directly without the interactive menu:

```bash
uv run python -m mlx_lm.lora \
  --model Qwen/Qwen3-1.7B \
  --train \
  --data data_set/ \
  --iters 500 \
  --adapter-path adapters/Qwen_Qwen3-1_7B \
  --batch-size 2 \
  --max-seq-length 2048
```

Replace the model name and adapter path as needed. The interactive `train.py` script does this for you automatically!

---

---

## 🎯 How It Works

### Training - Choose or Add Models

When you run `train.py`, it shows all downloaded models AND lets you enter new ones:

```
🤖 Available Downloaded Models:

  [1] Qwen/Qwen2.5-1.5B-Instruct
      (medium: iters=500, batch=2, seq=2048)
  [2] Qwen/Qwen3-1.7B
      (medium: iters=500, batch=2, seq=2048)
  [3] Qwen/Qwen3-4B-Instruct-2507
      (large: iters=500, batch=1, seq=2048)

  [4] Enter model name manually

Select a model (1-4): 4
Enter model name (e.g., Qwen/Qwen3-1.7B): microsoft/Phi-3-mini-4k-instruct
```

**Two ways to add models:**

1. **Pre-download:** `huggingface-cli download MODEL_NAME` (faster)
2. **Type it in:** MLX will download automatically when training starts

The script automatically determines optimal training settings based on model size (small/medium/large/xlarge).

Each model's adapters are saved in a separate folder: `adapters/Qwen_Qwen3-1_7B/`, etc.

### Inference

When you run `chat.py`, it automatically finds all trained models:

```
🤖 Available Trained Models:

  [1] Qwen/Qwen3-1.7B
      (6 checkpoint(s) in Qwen_Qwen3-1_7B)
  [2] Qwen/Qwen3-4B-Instruct-2507
      (6 checkpoint(s) in Qwen_Qwen3-4B-Instruct-2507)
  [3] Use base model (no fine-tuning)

Select a model (1-3):
```

Then you can choose which checkpoint (100, 200, 300, etc.) to use!

---

## 🔧 Advanced Configuration (Optional)

### Find Your Specific Chat ID

By default, the parser uses `CHAT_ID = 3`. To train on a different conversation, this was simply the origincal chat I was targetting, this chat will be completely different on your machine:

```bash
# Open the database
sqlite3 chat_db_goes_here/chat.db

# List all chats
SELECT ROWID, chat_identifier, display_name FROM chat;

# Exit
.quit
```

Then edit `src/data_collection/parser.py` and change `CHAT_ID` to your desired chat.

### Add More Models

To use any HuggingFace model:

```bash
# Download a model (optional - MLX will auto-download)
huggingface-cli download Qwen/Qwen2.5-3B-Instruct

# Or just enter the model name when prompted in train.py
```

The script will automatically detect it!

### Customize Training Configs

Edit `src/train.py` to adjust default training settings:

```python
# Size-based defaults
DEFAULT_CONFIGS = {
    "small": {"iters": 500, "batch_size": 2, "max_seq": 2048},
    "medium": {"iters": 500, "batch_size": 2, "max_seq": 2048},
    "large": {"iters": 500, "batch_size": 1, "max_seq": 2048},
    "xlarge": {"iters": 500, "batch_size": 1, "max_seq": 1024},
}

# Model-specific overrides
MODEL_CONFIGS = {
    "your-model/name": {"iters": 300, "batch_size": 1, "max_seq": 1024},
}
```

The script automatically categorizes models by size based on their name.

### Customize Parser

In `src/data_collection/parser.py`:

- `CHUNK_SIZE`: Messages per training example (default: 50)
- `TRAIN_RATIO`: Train/validation split (default: 0.9)

## 📁 Project Structure

```
finetuning_imessage_chats/
├── README.md              # You are here!
├── pyproject.toml         # Dependencies
├── chat_db_goes_here/     # Place your chat.db here
│   └── .gitkeep
├── data_set/              # Generated training data (gitignored)
│   ├── train.jsonl
│   └── valid.jsonl
├── adapters/              # Trained model weights (gitignored)
│   ├── Qwen_Qwen3-1_7B/  # Each model gets its own folder
│   │   ├── adapter_config.json
│   │   ├── 0000100_adapters.safetensors
│   │   └── ...
│   └── Qwen_Qwen3-4B-Instruct-2507/
│       └── ...
└── src/                   # All code lives here
    ├── train.py           # Interactive training script
    ├── chat.py            # Interactive chat interface
    └── data_collection/   # Database parsing
        └── parser.py
```

## ⚙️ Configuration

### Chat Bot Settings (`src/chat.py`)

```python
MAX_HISTORY = 15  # Number of conversation turns to remember
SYSTEM_PROMPT = """..."""  # Customize the bot's personality
```

### Parser Settings (`src/data_collection/parser.py`)

```python
CHAT_ID = 3           # Your iMessage chat ID
CHUNK_SIZE = 50       # Messages per training example
TRAIN_RATIO = 0.9     # 90% train, 10% validation
```

## 🐛 Troubleshooting

### "Database not found" error

Make sure you've copied `chat.db` to `chat_db_goes_here/`:

```bash
ls -la chat_db_goes_here/chat.db
```

### "Permission denied" when accessing chat.db

Grant Terminal "Full Disk Access" in System Preferences → Security & Privacy → Privacy → Full Disk Access.

### Model runs out of memory

Reduce `--batch-size` to 1 or use a smaller model:

```bash
--model Qwen/Qwen3-0.6B
```

### Bot generates weird responses

- Try different checkpoints (earlier ones might be better)
- Lower the temperature in `src/chat.py`: `sampler = make_sampler(temp=0.4)`
- Increase training iterations for more fine-tuning

## 📝 Tips

1. **Start small**: Begin with 500 iterations and test. Increase if needed.
2. **Monitor training**: Watch the loss values decrease during training.
3. **Try different checkpoints**: Earlier checkpoints (100-300) sometimes perform better than later ones.
4. **Adjust temperature**: Lower = more deterministic, higher = more creative (range: 0.1-1.5).
5. **Clean your data**: The parser filters out reactions and system messages, but you may want to customize the filters.

## 🔒 Privacy

⚠️ **Important**: Your `chat.db` contains all your personal messages. This repository's `.gitignore` is configured to exclude:

- Database files (`chat_db_goes_here/*.db`)
- Training data (`data_set/*.jsonl`)
- Model weights (`adapters/*.safetensors`)

Never commit these files to a public repository!

## 📄 License

MIT License - Feel free to use and modify as needed.

## 🙏 Acknowledgments

- [MLX](https://github.com/ml-explore/mlx) - Apple's ML framework
- [MLX-LM](https://github.com/ml-explore/mlx-examples) - Language model examples
- [Qwen](https://github.com/QwenLM/Qwen) - Base models from Alibaba Cloud

---
