import re
from pathlib import Path
from .policy import owner_adapter


class Model:
    def __init__(self, cfg):
        if not owner_adapter(cfg):
            raise ValueError('An owner-only model is required. Earlier mixed-context adapters are disabled; train a new version.')
        adapter = Path(cfg["adapter_path"])
        if not (adapter / "adapters.safetensors").is_file():
            raise ValueError("No trained adapter. Run export and train before generating replies.")
        import json
        metadata = json.loads((adapter / "personal-model.json").read_text())
        if metadata["model"] != cfg["model"]:
            raise ValueError("Adapter and base model do not match. Train again or restore the model setting.")
        from mlx_lm import load
        self.model, self.tokenizer = load(cfg["model"], adapter_path=str(adapter))

    def reply(self, turns, cfg):
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        prompt = self.tokenizer.apply_chat_template(
            [dict(role="system", content=cfg["system_prompt"])] + turns,
            tokenize=False, add_generation_prompt=True)
        text = generate(self.model, self.tokenizer, prompt=prompt, max_tokens=160,
                        sampler=make_sampler(temp=0.5), verbose=False)
        if "<think>" in text and "</think>" not in text:
            return ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if not text or len(text) > 1000 or any(x in text for x in ("<|", "[INST]", "</think>")):
            return ""
        return text
