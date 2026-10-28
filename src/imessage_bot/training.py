import json
import subprocess
import sys
from pathlib import Path

from . import config
from .messages import Messages, conversation, decode_text
from .state import State


def examples(rows, system):
    """Disjoint 6-hour sessions; one target assistant turn per example."""
    sessions, session, last_date = [], [], None
    for row in rows:
        date = row["date"]
        if last_date is not None and date - last_date > 6*3600*1_000_000_000:
            if session:
                sessions.append(session)
            session = []
        session.append(row)
        last_date = date
    if session:
        sessions.append(session)
    groups = []
    for session in sessions:
        turns = conversation(session)
        batch = []
        for i, turn in enumerate(turns):
            if turn["role"] != "assistant" or i == 0:
                continue
            start = max(0, i-7)
            while turns[start]["role"] != "user":
                start += 1
            batch.append({"messages": [{"role":"system", "content":system}] + turns[start:i+1]})
        if batch:
            groups.append(batch)
    return groups


def export(contacts=None):
    cfg = config.load()
    source = Messages(cfg["db_path"])
    state = State()
    groups = []
    try:
        for chat in source.chats():
            if contacts and config.normalize(chat["contact"]) not in contacts:
                continue
            if chat["service"] not in ("iMessage", "SMS"):
                continue
            rows = source.all_history(chat["chat_id"])
            # Generated replies must not become new examples of the owner's writing.
            # Drop the whole session around them so targets remain human-authored.
            filtered, session = [], []
            last_date = None
            generated = False
            for row in rows:
                if last_date is not None and row["date"]-last_date > 6*3600*1_000_000_000:
                    if not generated:
                        filtered.extend(session)
                    session, generated = [], False
                session.append(row)
                last_date = row["date"]
                if row["is_from_me"] and state.generated(chat["chat_id"], decode_text(row)):
                    generated = True
            if not generated:
                filtered.extend(session)
            groups.extend(examples(filtered, cfg["system_prompt"]))
    finally:
        state.close()
    if len(groups) < 3:
        raise ValueError("Need at least 3 separate conversation sessions with your replies. No dataset written.")
    # Sessions are indivisible, so overlapping context never crosses the split.
    import random
    random.Random(42).shuffle(groups)
    cut = max(1, min(len(groups)-1, int(len(groups)*0.9)))
    output = config.HOME / "data"
    config.private_dir(output)
    counts = []
    for name, partition in (("train", groups[:cut]), ("valid", groups[cut:])):
        entries = [item for group in partition for item in group]
        with (output / f"{name}.jsonl").open("w") as stream:
            for item in entries:
                stream.write(json.dumps(item, ensure_ascii=False)+"\n")
        counts.append(len(entries))
    print(f"Exported {counts[0]} training and {counts[1]} validation examples to {output}.")
    print("Assistant targets contain your outgoing messages. Review locally before training.")


def train(iters):
    cfg = config.load()
    if cfg["enabled"]:
        raise ValueError("Turn replies off before training.")
    if iters <= 0:
        raise ValueError("Iterations must be positive.")
    data = config.HOME / "data"
    if not all((data / (name+".jsonl")).is_file() for name in ("train", "valid")):
        raise ValueError("Run export first.")
    adapter = Path(cfg["adapter_path"])
    if (adapter / "adapters.safetensors").exists():
        raise ValueError("Adapter already exists. Set a new adapter_path in the private config to train a new model.")
    # MLX truncates sequences at max_seq_length. An oversized prompt could
    # otherwise truncate the entire assistant target and leave zero useful loss.
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(cfg["model"])
    prepared = config.HOME / "prepared_data"
    config.private_dir(prepared)
    for name in ("train", "valid"):
        entries = []
        # splitlines() also splits literal U+2028/U+0085 inside valid JSON strings.
        for line in (data / (name+".jsonl")).read_text().split("\n"):
            if not line.strip():
                continue
            entry = fit_example(json.loads(line), tokenizer)
            if entry:
                entries.append(entry)
        if not entries:
            raise ValueError(f"No {name} examples fit the token budget. Use shorter conversations.")
        (prepared / (name+".jsonl")).write_text("".join(json.dumps(x, ensure_ascii=False)+"\n" for x in entries))
        print(f"Prepared {len(entries)} {name} examples within 1024 tokens.")
    config.private_dir(adapter)
    subprocess.run([sys.executable, "-m", "mlx_lm", "lora", "--model", cfg["model"],
                    "--train", "--data", str(prepared), "--adapter-path", str(adapter),
                    "--iters", str(iters), "--batch-size", "1", "--max-seq-length", "1024",
                    "--mask-prompt", "--grad-checkpoint"], check=True)
    (adapter / "personal-model.json").write_text(json.dumps({"model":cfg["model"], "iterations":iters}))
    print("Training finished. Evaluate with chat and preview mode before live replies.")


def fit_example(entry, tokenizer, limit=1024):
    messages = list(entry["messages"])
    while len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)) > limit:
        if len(messages) <= 3:
            return None
        # Keep the system prompt and remove the earliest user/assistant pair.
        del messages[1:3]
    return {"messages": messages}
