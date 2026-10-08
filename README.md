# CC Switch Imagegen

An image-generation skill for Codex that routes image creation and editing through a local [CC Switch](https://github.com/farion1231/cc-switch) OpenAI-compatible Responses API relay.

## What it does

- Preserves the structured prompting workflow used by OpenAI's image generation skill.
- Generates and edits images through `POST /v1/responses` with the `image_generation` tool.
- Supports local input images, transparent backgrounds, quality, size, output format, and model overrides.
- Decodes and validates base64 PNG, JPEG, and WebP results without fetching arbitrary remote URLs.

## Install

Copy this directory into your Codex skills directory, or install the packaged `.skill` artifact from the releases/output of this repository.

The relay must be running at `http://127.0.0.1:15721/v1/responses` by default. The script resolves the newest model available to CC Switch at runtime, checking `/v1/models`, the local CC Switch/Codex catalogs, and the active Codex model. Override the endpoint with `CC_SWITCH_RESPONSES_URL`, the model with `CC_SWITCH_IMAGE_MODEL`, and the bearer token with `CC_SWITCH_BEARER_TOKEN`.

## Direct script usage

```bash
python3 scripts/cc_switch_imagegen.py \
  --prompt 'Use case: stylized-concept; Primary request: a nuclear-powered mechanical bull' \
  --quality medium \
  --size 1024x1024 \
  --output outputs/image.png
```

Use `--model <id>` when a particular image-capable model is required. Without an override, the default follows the active CC Switch model configuration and falls back to `gpt-6.1-sol` only when discovery is unavailable.

For editing or compositing, repeat `--input-image path/to/source.png`. The script sends local images as data URLs and never prints the bearer token or full API response.

## Compatibility

This skill expects a CC Switch relay that accepts the OpenAI Responses API `image_generation` tool and returns a completed `image_generation_call` whose `result` is base64 image data. Model availability depends on the upstream account group configured in CC Switch.

## License

MIT. See [LICENSE](LICENSE).
