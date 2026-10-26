---
name: Bug report (English)
about: It does not run, it raises, or the output is broken
title: "[BUG] "
labels: bug
---

## Environment

> 💡 **Fastest path**: run `curl -s 127.0.0.1:8188/h3relay/health` (adjust the port) and paste the JSON.
> It returns everything at once: pack version / API exit (V1 or V3) / registered node count / timing contract /
> optional upstream dependency / Python + torch / OS.
>
> ⚠ If that request does not connect, check the startup log for a line starting with
> `[H3 Relay] v…` — **no such line means the pack never loaded** (most common cause: the folder is nested
> two levels deep under `custom_nodes/`; it must be a **direct** child directory).

- Pack version (`__version__` in `__init__.py`, or the `version` field of `/h3relay/health`):
- ComfyUI version / commit date:
- Python / torch version:
- OS:

## What happens

(error text, last 20 lines of the log, or "where exactly the clip goes wrong")

```
paste the log here
```

## Reproduce

1. Which workflow (if it is one of the examples, say which parameters you changed)
2. Stage index / `run_id` / `mask_mode` / `settle_frames`
3. The segment number where it starts going wrong

## Ruled out

- [ ] Restarted the ComfyUI **backend** (not just the browser tab)
- [ ] Pasted the `/h3relay/health` JSON (or said why that request does not connect)
- [ ] Checked the troubleshooting table [docs/05-troubleshooting_EN.md](../../docs/05-troubleshooting_EN.md)
- [ ] (**only for `git clone` installs** — Manager / Registry installs ship no `tests/`)
      ran `python tests/test_relay_core.py`; quote the trailing "passed N / failed 0"
      — ⚠ do not quote a fixed total, it changes between versions

> ⚠ Please do not paste absolute local paths, model paths or any credentials (see SECURITY.md).
