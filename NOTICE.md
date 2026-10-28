# Source and attribution

This project builds on the local iMessage fine-tuning workflow from
[devk03/imessage-finetuning](https://github.com/devk03/imessage-finetuning),
snapshot `1912740dd315956ca436a5e65bdcf125a83de7eb`.

The original training, interactive chat and parser scripts and README are
preserved in `upstream/` for attribution and reference, with trailing whitespace
normalized. The upstream README
declares an MIT license, although this snapshot has no separate LICENSE file.
Its original author retains ownership of that material. This notice does not
relicense the upstream code or any downloaded model.

The new `src/imessage_bot/` implementation replaces the hardcoded chat and
speaker names with local configuration, structured assistant-target training,
and a Messages automation worker. No upstream databases, adapters, or weights
have been copied into this repository. Model licenses apply separately; the
default Qwen model card declares Apache-2.0.
