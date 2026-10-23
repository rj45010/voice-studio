# Voice Studio v1: voice packs and expressive speech

Goal: a cloned voice that sounds like a person talking, with pitch, energy and pace that change from sentence to sentence, instead of one flat read. Then use it for the 30 second reel.

Everything below is checked against the current code (`public/server.py`, `public/index.html`, `public/install.sh`) and the installed OmniVoice package. Anything not yet proven is marked **GATE** and is settled by step 0 before it is built.

---

## 0. Facts this plan rests on

Verified in the code:

| Fact | Where |
|---|---|
| The engine already builds the voice prompt once per job and reuses it for every sentence | `server.py` `run_job` |
| Sentences are generated one at a time, joined with a fixed 0.25 s gap | `server.py` `run_job` |
| `create_voice_clone_prompt` trims silence and adds end punctuation to the transcript by default | omnivoice `create_voice_clone_prompt(preprocess_prompt=True)` |
| A prompt can be saved and loaded (`VoiceClonePrompt.save/load`) | omnivoice `VoiceClonePrompt` |
| Output volume follows the reference clip's loudness when the reference is quiet (`ref_rms` under 0.1 scales the output down), so a soft calm sample gives quieter output than an excited one | `omnivoice/models/omnivoice.py` around line 898 |
| `generate()` accepts `language`, `speed`, `duration` per item | omnivoice `generate` |
| Inline tags `[laughter]`, `[sigh]` are kept as control text | omnivoice `utils/text.py` |
| `instruct` is a fixed list (gender, age, pitch, whisper, accent) meant for voice design without a reference. There is no free text emotion control | omnivoice `utils/voice_design.py` |
| About 650 language names are supported | omnivoice `utils/lang_map.py` |
| The Apple speech tool accepts a locale argument; the engine never passes one, so it always uses the Mac's system locale | `src/apple_speech.swift`, `server.py` `transcribe` |
| The sentence splitter only knows `. ! ? ।` and needs a space after them | `server.py` `split_sentences` |
| Re-running the installer replaces `server.py` and restarts the service | `install.sh` lines 24, 52 to 53, 61 |
| Voices are stored in IndexedDB `voice-studio` v1, store `voices`, record `{id, name, text, wav, seconds}` | `index.html` `db()` |

Two real bugs found while checking, fixed in this plan:
1. **Mandarin and Arabic never split.** `。！？` and `؟` are not in the splitter, and Chinese has no space after punctuation. A whole Chinese script becomes one sentence, which is slow and unstable. Fixed in section 4.
2. **Non-English Mac users get bad transcripts.** Apple speech runs in the system locale, not the language spoken. Fixed in section 3.

### What OmniVoice already does vs what this plan adds

Checked against OmniVoice 0.2.1 (installed) and the GitHub repo at commit `08be0b4` (24 Aug 2026), including its README, `docs/tips.md`, `docs/community-projects.md` and the Gradio demo.

Already in OmniVoice (reuse, don't claim as ours):
- Zero-shot cloning, reusable voice prompts, `speed`, `duration`, `language`.
- Non-verbal tags: `[laughter]`, `[sigh]`, `[confirmation-en]`, `[question-en]`, `[question-ah]`, `[question-oh]`, `[question-ei]`, `[question-yi]`, `[surprise-ah]`, `[surprise-oh]`, `[surprise-wa]`, `[surprise-yo]`, `[dissatisfaction-hnn]`.
- Long text chunking with cross-fade, only above 30 s of estimated audio.
- A Gradio demo: one reference, one text box, sliders. No moods, packs or direction parsing.
- `docs/tips.md`: when reference audio and `instruct` conflict, the model follows the reference. This is why mood comes from samples, not instructions.

New in this plan (not in OmniVoice or its demo):
- Voice packs with guided mood recordings, recording checks and the same-as-normal nudge.
- Mood per sentence from direction text, punctuation and brackets, with the direction removed from the audio and editable chips.
- Per sentence redo, playback while generating, loudness and pause joining.
- (From before, article 2) serverless site, local engine, installer, Apple speech transcription, speed learning.

Community projects listed upstream that overlap, read from the listing only, not inspected:
- `omnivoice-server`: OpenAI style server with voice profiles and sentence level streaming. Similar idea to streaming, server side.
- `Auris`: audiobook reader with per character voices. Different voices per character, not moods of one voice.
- `OmniVoice-MLX` and `MLX-Audio`: run OmniVoice on Apple's MLX instead of PyTorch. See G8.

**GATE G8 (speed):** try OmniVoice on MLX on the 8 GB M1 with the step 0 sentence. If it is clearly faster and sounds the same, plan an MLX engine path for Apple Silicon as its own release. Untested; no speed claim until measured.

Non-verbal tags in the mood rules (only if G3 passes): the page may add one tag per sentence at most, and only where the user did not write one: deadpan may start with `[dissatisfaction-hnn]`, excited with `[surprise-oh]` only when the direction word is "surprised" or "shocked". Default off; it ships only if Rahul likes it in the step 0 follow up.

### Article 2 updates when v1 ships

`~/Downloads/articles/voice-studio/02-voice-studio-ARTICLE.md` must then:
- Add a section on voice packs: why one sample sounds flat, the six guided moods, mood from direction text, with one real before and after example from Rahul's pack.
- Credit OmniVoice for tags, speed and cloning, and say clearly which parts are ours (list above).
- Replace "17 minutes for 24 seconds" only if G8 or other measured changes improve it, with the new measured numbers.
- Replace the "Lively, calm and Hinglish" teleprompter description with the pack flow and English/Hindi lines.
- Keep "Windows untested" and "Safari unconfirmed" unless they have been tested by then.

---

## Step 0. Listening test (before any building)

On the 8 GB M1, CPU, with the existing `me.wav` (lively) and `me_calm.wav` (calm). Same sentence for every row so only one thing changes.

| # | Text | Sample | Setting | Question it answers |
|---|---|---|---|---|
| 1 | `It actually worked.` | lively | default | baseline |
| 2 | `It actually worked!` | lively | default | does `!` change energy |
| 3 | `It ACTUALLY worked!` | lively | default | does CAPS change stress |
| 4 | `It actually worked. [laughter]` | lively | default | do tags work in clone mode |
| 5 | `It actually worked.` | calm | default | does the sample change mood (expected yes) |
| 6 | `It actually worked.` | calm | `speed=0.9` | does speed sound natural |
| 7 | `It actually worked?` | lively | default | does `?` lift the end |

Also measure, from the engine log: time to build one prompt, and time per sentence.

**GATES decided by this test:**
- G1: CAPS. If 3 sounds the same as 2, CAPS is **not** used as a cue and the help text never mentions it.
- G2: `!`. If 2 sounds the same as 1, `!` only picks the excited sample and is not claimed to change delivery by itself.
- G3: tags. If 4 does not laugh, `[laughter]` and `[sigh]` are stripped from the text and not mentioned.
- G4: speed. If 6 sounds unnatural, mood rules do not change speed.
- G5: prompt cost. If building a prompt takes over 5 s, prompts are cached on disk per pack (section 4.4). Otherwise they are built per job.

Rahul judges by ear. Results are written into this file before step 1.

---

## 1. What the user sees

### 1.1 Guided recording ("Make my voice pack")

One card, seven short steps, about two minutes in total.

| Step | Mood key | Prompt shown | Example line (English) |
|---|---|---|---|
| 1 | `normal` | Your normal voice | "I usually take the long way home, just to clear my head a little." |
| 2 | `excited` | Excited, like great news | "No way, we actually got it! I can't believe this is happening!" |
| 3 | `calm` | Calm and soft, late at night | "It's okay. Take your time, there's no rush at all tonight." |
| 4 | `deadpan` | Deadpan, totally unimpressed | "Oh great. Another meeting that could have been an email." |
| 5 | `serious` | Serious, like a warning | "Listen carefully. Do not open that door until I get back." |
| 6 | `curious` | Curious, like telling a secret | "Wait, did you hear that? I think someone's still inside." |
| 7 | (optional) | Read with as much feeling as you can | an original paragraph, see 1.3 |

Rules:
- Steps 1 and 2 are required. Steps 3 to 7 can be skipped. A pack with only `normal` works like today's single voice.
- Each step shows: the mood label, the line, Record, and after recording: play back, Redo, Next.
- A progress row of 7 dots shows done, skipped and current.
- Above the steps: a two-option switch for the lines, English or Hindi.
- Each line is 8 to 14 words, so a normal reading is 3 to 8 seconds, inside the 3 to 10 second sweet spot the page already warns about.

### 1.2 Checks after each recording (in the browser, on the decoded 24 kHz PCM)

Shown as a plain sentence under the clip. A failed check offers Redo, but the user can still keep it with "Use anyway", except for hard failures.

| Check | How | Message | Hard fail |
|---|---|---|---|
| Too short | speech length after trimming silence under 2.5 s | "Too short. Read the whole line." | yes |
| Too long | over 15 s | "That's long. Shorter clips clone better and keep generation fast." | no |
| Silent | RMS of whole clip under -50 dBFS | "We didn't hear anything. Check the microphone." | yes |
| Too quiet | speech RMS under -35 dBFS | "A bit quiet. Move closer to the mic." | no |
| Clipping | more than 0.1% of samples at or above 0.99 | "Too loud, it's distorting. Move back a little." | no |
| Noisy | noise floor (quietest 10% of 20 ms frames) less than 20 dB below speech level | "Lots of background noise. Try a quieter spot." | no |
| Wrong words | see 1.4 | "Sounds like you said something different. Check the text below." | no |

**Guidance copy**, shown so users act the moods instead of reading them:
- At the start of the pack: "Act it out. The clone copies how you say each line, not just your voice. If every line sounds the same, your clone will sound flat too."
- Under each mood label, one acting hint: excited "Smile while you say it", calm "Slow down, almost whisper", deadpan "Flat, like you couldn't care less", serious "Lower and firmer", curious "Lean in, like it's a secret".
- On the final step, play `normal` and `excited` back to back with the line: "Hear the difference? Bigger difference, better clone."

**Same-as-normal nudge** (after step 2 onward, compares each mood clip with the `normal` clip; it measures difference, not emotion):
- Measure per clip: speech loudness (RMS dB), pitch spread (standard deviation of pitch in semitones, pitch found by autocorrelation on 40 ms speech frames, 70 to 400 Hz, frames with weak correlation skipped), and pace (speech seconds per word of the line).
- Expected direction per mood: excited louder or wider pitch or faster; calm quieter or slower; deadpan narrower pitch; serious lower average pitch or quieter; curious wider pitch or slower.
- If none of the expected changes reach a small margin (loudness 2 dB, pitch spread 15%, average pitch 1 semitone, pace 10%), show: "This sounds a lot like your normal take. Try exaggerating it a bit more." with Redo and "Keep it".
- Never a hard fail. People speak differently and the measures are rough. If pitch can't be measured (too few voiced frames, whisper), skip the pitch part silently.
- **GATE G7:** before shipping, run the nudge on Rahul's own pack and on one deliberately flat pack. It must flag the flat pack and stay quiet on the acted one. If it doesn't, ship only the guidance copy.

Speech vs silence: 20 ms frames, a frame is speech if its RMS is within 30 dB of the loudest frame. Trim leading and trailing silence to 150 ms before saving (OmniVoice also trims; trimming here makes the length check honest).

Microphone settings: request `getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false}})`. Noise suppression and auto gain change the voice's texture and level, and the clone copies those artifacts. If the browser rejects the constraints, fall back to `{audio: true}`. **GATE G6:** record one sample each way during step 0 follow up and compare the clones. Keep whichever sounds closer.

### 1.3 The paragraph (step 7)

An original paragraph, one sentence per mood in a fixed order: normal, excited, calm, deadpan, serious, curious. Shown one sentence per line with the instruction "Pause for a breath between lines."

Use: cut it into six extra clips so each mood has a second sample.

How to cut it, without depending on speech recognition:
1. Find silences of at least 350 ms (same speech/silence rule as 1.2).
2. If the number of speech runs between silences equals 6, each run is one mood clip.
3. If not, try 300 ms and 450 ms. If still not 6, keep the paragraph unsplit and **do not use it** for any mood. Tell the user: "Couldn't split the paragraph cleanly, your six main clips are still used." Nothing breaks.
4. Each cut clip goes through the same checks as 1.2. Clips that fail a hard check are dropped.

Which sample is used when a mood has two: the one with the better check result (fewer warnings), then the longer one if tied. Fixed choice, no randomness, so the same script gives the same voice.

**Release:** the paragraph is v1.1, not v1. The six direct clips already give the range; the paragraph is a quality bonus with the most moving parts.

### 1.4 Transcript

- Read-aloud lines: the transcript is the line shown. No typing.
- Then speech recognition runs anyway, only to compare. Compare normalised words (lowercase, strip punctuation). If fewer than 70% of the line's words appear in the recognised text, show the "Wrong words" warning and switch the text box to the recognised text for the user to fix. Recognition failure never blocks saving; it only skips the comparison.
- Uploaded files and "say anything" (v2): the transcript is the recognised text, editable.
- The engine must pass the right locale to Apple speech (`en-US` or `hi-IN`, from the line language switch). If Apple speech says the locale is unsupported (the Swift tool exits with that message), fall back to Whisper small. Whisper gets the language too, through `generate_kwargs={"language": ...}`.

### 1.5 Writing the script, with direction

The user writes normally. The page decides a mood per sentence and shows it as a chip before generating.

```
"I'm fine," I said sarcastically.     → speaks "I'm fine."         chip: deadpan · from "sarcastically"
This is HUGE news!                    → speaks it as written        chip: excited · from "!"
Do you hear that?                     → speaks it as written        chip: curious · from "?"
Okay.                                 → speaks it as written        chip: normal
```

Tap a chip to change the mood (menu of the pack's moods). A manual choice is never overwritten by the detector until the sentence's text changes.

The "?" button next to the Script title opens a small panel:
- "Write it like a story. `"I'm fine," I said sarcastically.` reads only "I'm fine" in a deadpan voice."
- "End with `!` for excitement or `?` for curiosity."
- Three example lines; tapping one inserts it at the cursor.
- Only cues that passed step 0 are listed (G1 to G3).

### 1.6 Generating

- Each sentence becomes a row: text, mood chip, status (waiting, generating, ready, failed), play, ↻ redo.
- Sentence 1 plays as soon as it is ready while the rest continue. "Play all" plays finished sentences in order and waits for the next if it isn't ready.
- ↻ on one sentence regenerates only that sentence with a new random take. Allowed only while no other job is running.
- Cancel stops after the current sentence (generation of one sentence can't be interrupted mid-way).
- Download gives one WAV of all ready sentences, joined as in 4.3. Download is disabled until every sentence is ready or the user removes failed ones.

---

## 2. Data

### 2.1 IndexedDB v2

Bump `indexedDB.open("voice-studio", 2)`. In `onupgradeneeded`, if `oldVersion < 2`, migrate each record:

```
v1 record                     v2 pack
{id, name, text, wav,     →   {id, name, version: 2, created,
 seconds}                      moods: {normal: [{text, wav, seconds, lang, checks}]}}
```

- Migration runs inside the upgrade transaction, so it is all or nothing.
- A migrated voice behaves exactly like before: every sentence uses `normal`.
- Moods are arrays to hold the second sample from the paragraph.

Other tabs: when one tab upgrades, other open tabs get `versionchange`. Handle it by closing the db and showing "Voice Studio was updated in another tab. Reload." Otherwise the upgrade is blocked silently.

Storage safety:
- Call `navigator.storage.persist()` once after the first pack is saved. If refused, nothing changes, but show the export tip (2.2) more prominently.
- Catch `QuotaExceededError` on save: "Your browser is out of space for voices. Delete an old voice or export it first." A pack is about 7 clips × 8 s × 48 KB/s ≈ 2.7 MB, so quota problems are rare but possible in private windows.
- Private or incognito windows: IndexedDB may be cleared on close. Show "This is a private window. Voices will be lost when it closes." when `navigator.storage.estimate()` reports a small quota (under 120 MB is the usual private window signal). This is a heuristic, so it is a tip, not a block.

### 2.2 Export and import

- Format: one `.voicepack.json` file: `{format: "voice-studio-pack", version: 2, name, created, moods: {mood: [{text, lang, seconds, checks, wav_b64}]}}`. About 3.6 MB. JSON instead of zip because zip needs a library or a hand-written writer, and the size is fine.
- Import validates before writing anything: format string, version 2, every mood key in the known list, every `wav_b64` decodes to a WAV that `toWav` can read, 2 to 20 s per clip, at most 3 clips per mood, total under 25 MB. Any failure: "This file isn't a Voice Studio pack" with nothing saved.
- Imported packs get a new `id`. Name collision: append " (2)", " (3)".
- After the first pack is saved, show once: "Your voice lives only in this browser. Download a backup?"

---

## 3. Languages

English and Hindi only, decided by Rahul. Both he can check himself.

- Read-aloud lines: English and Hindi (Devanagari). The current Hinglish line is replaced by the Hindi set; Hinglish scripts still work because the sample's language does not limit the output language (Rahul's own test: English sample, good Hindi output).
- Script language: a small switch, Auto / English / Hindi, sent as `language` to `generate`. Default Auto, which matches today's behaviour. Hinglish scripts should use Auto.
- More languages later only with lines checked by a native speaker. Not planned now.

---

## 4. Engine (`server.py`)

### 4.1 Version handshake

`/api/status` adds `"api": 2`. The page checks `api`:
- missing or 1: everything works in the old single voice mode, and a banner says "Update the engine to use voice packs" with the same install command. The installer already overwrites and restarts, so updating is one paste.
- 2: full features.

The new page must never send v2 requests to a v1 engine. Old engine with new page is the most likely real world state for weeks.

### 4.2 Jobs API v2

`POST /api/jobs` with:

```json
{
  "api": 2,
  "language": "Hindi" | null,
  "moods": {"normal": {"text": "...", "wav_b64": "..."}, "deadpan": {...}},
  "sentences": [{"text": "I'm fine.", "mood": "deadpan", "pause": 0.35, "speed": 1.0}]
}
```

- Only moods actually used by the sentences are sent. Keeps the body small (well under the 20 MB limit) and avoids building unused prompts.
- Validation, all returning 400 with a plain message: 1 to 200 sentences, each 1 to 600 characters after trimming, every `mood` present in `moods`, `pause` between 0 and 2, `speed` between 0.7 and 1.3, `language` one of null, "English", "Hindi".
- The page does the splitting and mood picking. The engine trusts the sentence list and does no text parsing of its own (except the v1 path, kept unchanged).

Job state, per sentence: `{status: waiting|running|done|error, error}`. Audio per sentence is kept in memory as float32.

New routes:
- `GET /api/jobs/{id}`: overall status plus the per-sentence statuses.
- `GET /api/jobs/{id}/sentences/{n}`: WAV of one finished sentence (404 until ready).
- `POST /api/jobs/{id}/sentences/{n}/redo`: regenerate one sentence. 409 if any job or redo is running. Optional `mood` in the body to change mood at the same time.
- `POST /api/jobs/{id}/cancel`: sets a flag checked between sentences. Status becomes `cancelled`. Finished sentences stay available.
- `GET /api/jobs/{id}/audio`: the joined WAV of all done sentences, built on request (4.3).

Kept: only the latest job lives in memory (same as today). Starting a new job discards the old one. The page warns before that: "Start a new script? The current audio will be cleared. Download it first?"

### 4.3 Joining sentences

For each sentence, in order:
1. Loudness match: scale every sentence to a fixed speech level (RMS 0.08), capped at +9 dB so a near silent take is not blown up into noise. Built with a fixed target instead of the job median so each sentence can be streamed the moment it is ready, at its final volume. This fixes the louder-mood, quieter-mood jumps that `ref_rms` causes.
2. Pause after it, chosen by the page from how the sentence ends: `,` 0.20 s, `.` 0.35 s, `!` or `?` 0.40 s, `…` or `...` 0.70 s, end of paragraph (blank line) 0.90 s. Sent as `pause`, so the engine just inserts it.
3. 10 ms fade in and fade out on each sentence to avoid clicks at the joins.
4. Final peak limit: if the joined audio peaks above 0.98, scale the whole thing down to 0.98. No clipping in the download.

### 4.4 Prompts

Per job, build one `VoiceClonePrompt` per used mood, lazily, the first time a sentence needs it. If G5 shows prompt building is slow, cache prompts in `~/.voice-studio/prompts/{sha256 of wav + text}.pt` with `VoiceClonePrompt.save`, load with `.load`. The hash key means an edited sample never reuses a stale prompt. Cap the cache at 50 files, delete oldest first.

### 4.5 Sentence splitting (page side, replaces the engine's for v2)

Split after `. ! ? … । 。 ！ ？ ؟` and after a blank line. Do not split:
- inside quotes until the quote closes (`"I'm fine," I said.` stays one sentence),
- after common abbreviations (`Mr. Mrs. Ms. Dr. St. vs. etc. e.g. i.e.`), English only,
- between digits (`3.5`),
- after a single capital letter and a dot (`J. K. Rowling`).

For `。！？` no following space is needed. Sentences longer than 600 characters are split at the nearest `,` `;` or `、` before 600, otherwise at the nearest space, otherwise hard at 600. Keep this in one function with a small test list (section 6).

### 4.6 Idle unload and concurrency

- The idle unloader already skips while a job is `running`. Extend it to also skip while a redo is running, and to treat a job with waiting sentences as busy.
- `model_lock` stays the single lock around loading. Generation still runs in one worker thread per job; a redo waits behind nothing because only one may run (409 otherwise).
- Two tabs open: both see `busy` from `/api/status` and show "Another tab is generating" instead of a confusing 409.

---

## 5. Mood picking rules (page side)

Run on each sentence after splitting. First match wins:

1. **Manual choice** on the chip.
2. **Direction outside the quotes.** If the sentence has quoted speech (`"..."`, `“...”`, `«...»`, `「...」`) and text outside it, the outside text is direction. It is removed from what is spoken. The mood comes from words in the direction (list below). If no word matches, mood is `normal` and the direction is still removed, because the user wrote it as narration of how to say the line. If the quoted part is empty, nothing is removed and the sentence is spoken as written.
3. **Bracket direction.** `(sarcastic) I'm fine.` or `[whispering] come here.` The bracket is removed and its word picks the mood. Exceptions: `[laughter]` and `[sigh]` pass through untouched if G3 passed.
4. **Ending.** `!` gives `excited`, `?` gives `curious`. Both `?!` gives `excited`.
5. **CAPS** (only if G1 passed): two or more fully capitalised words of 3+ letters gives `excited`. Ignore known acronyms (any capitalised word of 2 to 5 letters that appears capitalised everywhere in the script).
6. Otherwise `normal`.

If the chosen mood has no sample in the pack, use the closest one that exists: excited → normal; calm → normal; deadpan → calm → normal; serious → deadpan → normal; curious → calm → normal. The chip shows it honestly: "deadpan (using calm, no deadpan sample)".

Direction word lists, English v1 (Hindi added with the Hindi lines):
- excited: excited, excitedly, shouted, yelled, screamed, cheered, thrilled, happily
- calm: calmly, softly, gently, quietly, slowly, tenderly
- deadpan: sarcastically, sarcastic, dryly, flatly, deadpan, bored, unimpressed, rolled my eyes
- serious: seriously, firmly, sternly, warned, coldly, angrily
- curious: curiously, wondered, whispered, asked, suspiciously, nervously

Matching is whole word, case insensitive. The list is data in one object so other languages are added without code changes.

Speed (only if G4 passed): calm 0.92, deadpan 0.95, excited 1.05, others 1.0.

---

## 6. Tests

Kept small and runnable, in the style of the repo.

- `tests/split.html` (opens in a browser, asserts in the console): 25 cases for the splitter and mood picker. Includes: abbreviations, decimals, initials, nested and curly quotes, `?!`, `…`, Hindi `।`, a Hinglish sentence in Latin letters, empty quotes, direction with no known word, bracket tags, CAPS with acronyms, a 900 character sentence.
- `tests/test_server.py` (stdlib `unittest`, model mocked): request validation (every 400 path), version in status, cancel between sentences, redo while busy gives 409, join math (median loudness, gain cap, pauses, peak limit) on synthetic sine waves.
- Five fixed listening scripts in `tests/listen/` (plain, sarcastic, excited, question, Hindi/English mix). Run after every engine change; Rahul listens. Results are not automated, on purpose.
- Manual checklist before release: Chrome, Brave, Edge on Mac; Chrome on Windows (the installer is still untested there, say so in the release note); one old v1 voice migrated; one pack exported and imported in another browser; engine v1 with page v2; private window.

---

## 7. Release order

1. **Step 0** listening test, plus G8 (MLX speed). Write results into section 0.
2. **v1:** guided recording (6 moods, English and Hindi), checks, IndexedDB v2 with migration, export and import, engine API v2 with handshake, per sentence rows with play and redo, playback while generating, cancel, joining with loudness and pauses, mood chips with the rules that passed step 0, "?" help, locale fix for Apple speech, splitter fix.
3. **Reel:** record Rahul's pack, generate the reel's clone lines, build the stickman reel.
4. **Article 2 update** (section 0, "Article 2 updates").
5. **v1.1:** paragraph step with silence cutting.
6. **v2:** "Say anything" recording mode. MLX engine path if G8 passed.

Each release is one or more commits on `main` in `~/voice-studio`, deployed with `wrangler deploy`. Commits as rj45010@gmail.com, no AI attribution.

---

## 8. Risks that remain after this plan

- **Mood still depends on the sample.** The guidance copy and the same-as-normal nudge (1.2) catch obvious flat takes, but they measure loudness, pitch and pace, not emotion. A take can differ on all three and still not sound excited. Users can always keep a take the nudge flagged.
- **Takes vary.** The model is random per run. Redo per sentence is the fix, not a guarantee.
- **Speed.** Measured during the v1 build on the 8 GB M1: about one minute per line, even for "Okay.", because every call reprocesses the reference sample. Cost is per line more than per character, so the per character estimate is rough. Playback while generating hides it, it doesn't remove it. G8 (MLX) is the real fix to try.
- **Windows and Safari** remain untested (unchanged from today).
- **English and Hindi only.** Scripts in other languages still generate (Auto), but direction words aren't recognised there; they fall back to punctuation and manual chips.
