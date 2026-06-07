# Asset-generation prompts

Prompt files for generating GOSE's motion/audio brand assets with AI tools.
Each `.txt` is a self-contained prompt (or prompt set) ready to paste.

- **Audio** (voice-over, SFX, music): ElevenLabs covers all of it.
  Sound-design language + the existing system-sound prompt set live in the
  owner's sound-prompts doc; these files follow the same sonic identity:
  **onyx / controller-first / warm**, brand motif **C–G–C**.
- **Animation/video**: tool-agnostic prompts (Runway / Pika / Sora-class
  generators all accept this shape). Use the brand crystal renders as the
  reference image wherever a tool accepts one.

Reference imagery: the GOSE crystal mark (blue-violet faceted gem, glowing
pixel core, onyx background) — the same art used for the launcher icon and
boot/OOBE logo in `gui/mockup/assets/`. Attach the black-background version
unless a file says otherwise.

| File | Asset | Where it lands in the OS |
|------|-------|--------------------------|
| 01-boot-animation.txt | Rotating-crystal boot loop | boot splash (kills the black gaps + stale-logo flash) |
| 02-setup-complete-animation.txt | OOBE finale | end of the first-boot wizard |
| 03-os-trailer-ad.txt | 30–45s product trailer | store page / marketing |
| 04-ui-microanimations.txt | Window/cursor motion references | windowing + cursor polish |
| 05-voiceover-lines.txt | Trailer + OOBE VO lines | trailer; optional wizard greeting |

After generating: drop results in a `generated/` folder beside this README
(gitignored if heavy), then they get wired into the OS deliberately — every
clip/animation goes through the same verify-on-device pass as code.
