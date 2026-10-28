import sqlite3
import json
import random
from pathlib import Path

# --- CONFIGURATION ---
DB_PATH = Path(__file__).parent.parent.parent / "chat_db_goes_here" / "chat.db"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "data_set"
CHAT_ID = 3  # Change this to your specific chat ID

# Training Split Configuration
TRAIN_RATIO = 0.9
RANDOM_SEED = 42

# How many messages to group together in one training example
# Larger = model sees more context but uses more memory
CHUNK_SIZE = 50

# 🚨 THE BAN LIST 🚨
BAD_STARTS = (
    "Loved “", "Emphasized “", "Liked “", "Disliked “",
    "Laughed at “", "Questioned “", "The recipient",
    "Reacted ", "Did you like an", "Loved an",
    "Liked an", "Laughed at an", "Emphasized an",
    "Disliked an", "Questioned an",
    "Unsent", "￼"
)
BAD_EXACT = ("￼", "")

def normalize_text(text):
    if not text: return ""
    text = text.replace('￼', '')
    return text.replace('\u2028', '\n').replace('\u2029', '\n').strip()

def is_clean(text):
    if not text: return False
    if text in BAD_EXACT: return False
    for bad in BAD_STARTS:
        if text.startswith(bad): return False
    return True

def parse_chat_messages(db_path, chat_id):
    if not Path(db_path).exists():
        print(f"Error: Database not found at {db_path}")
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # We get the handle_id to know WHO is speaking
    # handle_id = 0 is usually "Me" (the user)
    query = """
    SELECT m.is_from_me, m.text, h.id as handle_id
    FROM message m
    LEFT JOIN handle h ON m.handle_id = h.ROWID
    INNER JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
    WHERE cmj.chat_id = ?
      AND m.text IS NOT NULL
    ORDER BY m.date ASC
    LIMIT 100000
    """

    cursor.execute(query, (chat_id,))
    rows = cursor.fetchall()
    conn.close()

    messages = []
    for row in rows:
        raw_text = row['text']
        if raw_text:
            cleaned_text = normalize_text(raw_text)
            if is_clean(cleaned_text):
                # Assign a label.
                # You might want to map specific handle_ids to real names if you know them.
                # For now: 0 = "Me", others = "Friend"
                if row['is_from_me'] == 1:
                    sender = "Dev" # Change this to your name
                else:
                    sender = "Friend" # Or use row['handle_id'] if you want unique IDs

                messages.append(f"{sender}: {cleaned_text}")

    return messages

def create_text_chunks(messages, chunk_size=50):
    """
    Groups messages into large blocks of text.
    """
    dataset = []

    # Sliding window: move forward by half a chunk to give overlapping context
    step = int(chunk_size / 2)

    for i in range(0, len(messages) - chunk_size, step):
        # Join N messages into one long string
        chunk = "\n".join(messages[i : i + chunk_size])

        # MLX expects a "text" key for raw training
        dataset.append({"text": chunk})

    return dataset

def split_and_write(final_data, output_dir, train_ratio, random_seed):
    random.seed(random_seed)
    random.shuffle(final_data)

    split_index = int(len(final_data) * train_ratio)
    train_data = final_data[:split_index]
    valid_data = final_data[split_index:]

    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_dir / "train.jsonl", 'w', encoding='utf-8') as f:
        for entry in train_data:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    with open(output_dir / "valid.jsonl", 'w', encoding='utf-8') as f:
        for entry in valid_data:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"✅ Created {len(train_data)} training blocks and {len(valid_data)} validation blocks.")

def main():
    raw_msgs = parse_chat_messages(str(DB_PATH), CHAT_ID)
    print(f"Total messages found: {len(raw_msgs)}")

    # Create training chunks
    final_data = create_text_chunks(raw_msgs, CHUNK_SIZE)

    split_and_write(final_data, OUTPUT_DIR, TRAIN_RATIO, RANDOM_SEED)

if __name__ == "__main__":
    main()
