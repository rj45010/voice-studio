# Voice Studio

Clone your voice in the browser. The site is a static page; a local engine on your own computer does the generation, so your audio never leaves your machine.

Live at https://voice-studio.seemslegit.workers.dev

Built on [OmniVoice](https://github.com/k2-fsa/OmniVoice) by k2-fsa, installed as a dependency, not included in this repo. OmniVoice's code is Apache-2.0, but its [model weights are CC-BY-NC](https://huggingface.co/k2-fsa/OmniVoice#license): non-commercial use only. The installer downloads them from Hugging Face onto the user's machine; this project does not redistribute them.

## What you may do with the audio

Voice Studio does not grant you rights to the model. You use the model under its own licenses, which you agree to when the installer downloads it.

- **Non-commercial only.** The [model card](https://huggingface.co/k2-fsa/OmniVoice#license) licenses the weights CC-BY-NC "due to constraints from its training data (e.g., Emilia)", and the original Emilia dataset is [CC BY-NC 4.0](https://github.com/open-mmlab/Amphion/blob/main/preprocessors/Emilia/README.md). [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/legalcode.en) defines NonCommercial as "not primarily intended for or directed towards commercial advantage or monetary compensation". The licenses don't say whether generated audio falls under them. Assume it does. In practice, don't use Voice Studio audio in monetized videos, ads, sponsored posts, client work, paid courses, or anything you sell. For any of those, get permission from k2-fsa or use a model whose weights allow commercial use.
- **Credit the model.** CC-BY requires attribution. When you publish audio, credit it as: `Voice generated with OmniVoice by k2-fsa (CC-BY-NC), https://huggingface.co/k2-fsa/OmniVoice`.
- **Clone only voices you have permission to use.** That means your own voice, or someone else's with their consent. The OmniVoice card prohibits "unauthorized voice cloning, voice impersonation, fraud, scams". Laws on voice likeness and deepfakes vary by country, and you are responsible for following yours.
- **Audio tokenizer terms.** The model's audio tokenizer is under the [Boson Higgs Audio 2 Community License](https://huggingface.co/k2-fsa/OmniVoice/blob/main/audio_tokenizer/LICENSE), which is based on the Meta Llama 3 Community License. That license includes the Llama 3 [Acceptable Use Policy](https://llama.meta.com/llama3/use-policy). It forbids using outputs to improve any other large language model. Products with more than 100,000 annual active users need an expanded license from Boson AI.

Built with Higgs Materials licensed from Boson AI USA, Inc., Copyright Boson AI USA, Inc., All Rights Reserved and Meta Llama 3 licensed under the Meta Llama 3 Community License, Copyright Meta Platforms, Inc., All Rights Reserved.

The engine and model are provided as is, with no warranty. This section summarizes the licenses and is not legal advice; the license texts linked above govern.

## License

Copyright (c) 2026 Rahul Jangid. All rights reserved. The code is public to read; no license is granted to copy, modify or redistribute it.
