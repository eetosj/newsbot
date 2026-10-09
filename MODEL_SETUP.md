# Non-thinking model setup

The researched candidate was `qwen3:4b-instruct-2507-q4_K_M`.
The learner installed the non-thinking GGUF under the local tag
`qwen3-instruct-local:latest`; the bot now uses `qwen3-instruct-local` by default.

- [Qwen's model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)
  explicitly identifies this version as non-thinking only.
- [Ollama's model entry](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M)
  lists 4B parameters, Q4_K_M quantization, and about 2.5 GB of model weights.
- Finnish and English relevance/summary quality still need evaluation on actual
  collected items. The size of weights is not the complete GPU memory requirement.

Both `qwen3-local:latest` and `qwen3-instruct-local:latest` are installed. The
learner performed the installation. The agent's stalled download request was
cancelled after installation, and the original model remains available.

## Verification results

- Metadata identifies `qwen3-4b-instruct.gguf`, 4B parameters, Q4_K_M quantization.
  Its imported capability/template metadata still contains thinking indicators,
  so runtime output was checked rather than relying on those labels alone.
- Tiny JSON probe: `{"ready": true}`, `finish=stop`, six generated tokens,
  zero thinking characters.
- Previously failing Finnish article: valid decision and summary, `finish=stop`,
  277 generated tokens, 20.5 seconds.
- English paper abstract: valid decision and preview, `finish=stop`,
  190 generated tokens, 7.7 seconds.
- Both article samples entered ready state in a temporary database with zero
  evaluation errors. The learner's default article queue was not changed by testing.
- `config.example.json` now selects the local instruct tag and retains `think: false`.

Evaluate a small batch with the configured default:

```sh
.venv/bin/python -m newsbot --evaluate --limit-items 3
```

This evaluates the default database. No Discord or Telegram messages are sent by
evaluation. These two samples establish functional operation; broader relevance
and summary quality still need review.
